"""
Phase 5 deliverable: pick a set of admitted challenges for one repo, write a
title + description for each, and package them with that copy.

Usage:
    python scripts/demo_describe.py <repo_dir> <package> <venv_python>
        [--selection results.json] [--count 40 | --all] [--output-dir phase5_output]

--count draws a STRATIFIED sample across the score range, not the top N. Taking
the top N by score gave a course with no easy end at all (the easiest admitted
mutation never got packaged), which made "ordered easiest first" start in the
middle. --all packages every admitted challenge and is the better default for a
demo bank whenever the admitted set is small enough to package in one go.

--selection reuses a saved run_selection result (the slow step). Without it,
the selection runs here and is saved to <output-dir>/selection.json.
Set BUGFORGE_DISABLE_BEDROCK=1 to force the template for every challenge.

Every run also writes <output-dir>/run_report.json: the rejection taxonomy,
the test-gap list, per-stage timings, and the score components for each
admitted challenge. Those are the numbers the writeup and the blog post need,
and they used to exist only as stdout.
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from bugforge.baseline import compute_baseline, is_cached
from bugforge.models import MutationSite
from bugforge.languages import get_adapter
from bugforge.models import RunnerConfig
from bugforge.mutate import MutationError
from bugforge.package import package_challenge
from bugforge.run_report import Stopwatch, build_run_report, format_taxonomy, write_run_report
from bugforge.select import ClassificationResult, Outcome, ScoreBreakdown, run_selection
from cloud import describe as describe_module
from cloud.ids import challenge_id


# The demo covers the Python repo only; the pipeline itself takes whichever
# adapter it is handed (see bugforge/languages/).
adapter = get_adapter()


def _run_selection(repo_dir: Path, package: str, python: str, baseline, watch: Stopwatch) -> tuple[list[dict], dict]:
    pairs = []
    candidates_generated = 0
    generate_started = time.perf_counter()
    for py_file in adapter.discover_sources(repo_dir / package):
        rel = str(py_file.relative_to(repo_dir)).replace("\\", "/")
        source = py_file.read_text(encoding="utf-8")
        sites = adapter.find_candidates(source, rel)
        candidates_generated += len(sites)
        for site in sites:
            if not baseline.tests_for_line(site.path, site.lineno):
                continue
            try:
                pairs.append((site, adapter.apply(source, site)))
            except MutationError:
                continue
    watch.record("generate_candidates", time.perf_counter() - generate_started)

    print(f"running {len(pairs)} covered candidates through selection...")
    with watch.stage("selection_total"):
        results, _ = run_selection(
            repo_dir, adapter, RunnerConfig(package=package, python=python), baseline, pairs
        )

    records = [
        {
            "site": asdict(r.site),
            "outcome": r.outcome,
            "covering_tests": r.covering_tests,
            "failing_tests": r.failing_tests,
            "total_tests": r.total_tests,
            "traceback": r.traceback,
            "representative_test": r.representative_test,
            "score_breakdown": asdict(r.score_breakdown) if r.score_breakdown else None,
            "reason": r.reason,
            # kept so a --selection reuse can still report the timing split
            "targeted_seconds": round(r.targeted_seconds, 3),
            "full_suite_seconds": round(r.full_suite_seconds, 3),
            "full_suite_ran": r.full_suite_ran,
        }
        for r in results
    ]
    counts = {
        "candidates_generated": candidates_generated,
        "candidates_on_covered_lines": len(pairs),
    }
    return records, counts


# Sampling buckets. These are the edges the *course* is stratified on; the API's
# display label (cloud/handlers/fn_api.py:difficulty_label) uses 5.0/7.0 and is
# deliberately left alone here -- see the note in select_challenges.
BUCKET_EDGES = (4.5, 6.5)
BUCKET_NAMES = ("easy", "medium", "hard")


def bucket_of(score: float) -> str:
    """easy < 4.5 <= medium <= 6.5 < hard."""
    if score < BUCKET_EDGES[0]:
        return BUCKET_NAMES[0]
    if score <= BUCKET_EDGES[1]:
        return BUCKET_NAMES[1]
    return BUCKET_NAMES[2]


def _allocate(sizes: dict[str, int], total: int) -> dict[str, int]:
    """Proportional allocation by largest remainder, capped at each bucket size.

    Largest-remainder rather than rounding because a bucket holding 4 of 56
    admitted rounds to zero at any sample size below 14, which is exactly the
    easy end we are trying not to lose.
    """
    pool = sum(sizes.values())
    if pool == 0:
        return {name: 0 for name in sizes}
    exact = {name: size * total / pool for name, size in sizes.items()}
    take = {name: min(int(value), sizes[name]) for name, value in exact.items()}
    # Hand out what rounding dropped, biggest fractional part first.
    order = sorted(sizes, key=lambda name: (-(exact[name] - int(exact[name])), name))
    while sum(take.values()) < min(total, pool):
        for name in order:
            if sum(take.values()) >= min(total, pool):
                break
            if take[name] < sizes[name]:
                take[name] += 1
    return take


def _spread(records: list[dict], count: int) -> list[dict]:
    """`count` records spanning the list evenly, endpoints included."""
    if count >= len(records):
        return list(records)
    if count == 1:
        return [records[0]]
    step = (len(records) - 1) / (count - 1)
    return [records[round(i * step)] for i in range(count)]


def select_challenges(admitted: list[dict], count: int | None) -> list[dict]:
    """Stratified sample of the admitted set, easiest first.

    count is None for --all. Within a bucket the picks are spread evenly across
    that bucket's own score range rather than taken off the top, so a sampled
    course still shows the full spread of each tier.
    """
    by_score = sorted(admitted, key=lambda r: (r["score_breakdown"]["score"],
                                               r["site"]["path"], r["site"]["lineno"]))
    if count is None or count >= len(by_score):
        return by_score

    buckets = {name: [] for name in BUCKET_NAMES}
    for record in by_score:
        buckets[bucket_of(record["score_breakdown"]["score"])].append(record)

    take = _allocate({name: len(rows) for name, rows in buckets.items()}, count)
    chosen: list[dict] = []
    for name in BUCKET_NAMES:
        chosen.extend(_spread(buckets[name], take[name]))
    return sorted(chosen, key=lambda r: (r["score_breakdown"]["score"],
                                         r["site"]["path"], r["site"]["lineno"]))


def _classification(record: dict) -> ClassificationResult:
    """Rebuilds a ClassificationResult from a selection.json record.

    Drops and gaps have no score_breakdown, so this has to work without one --
    the run report classifies every candidate, not just the admitted ones.
    """
    breakdown = record.get("score_breakdown")
    return ClassificationResult(
        site=MutationSite(**record["site"]),
        outcome=record["outcome"],
        covering_tests=record["covering_tests"],
        failing_tests=record["failing_tests"],
        total_tests=record["total_tests"],
        traceback=record["traceback"],
        representative_test=record["representative_test"],
        score_breakdown=ScoreBreakdown(**breakdown) if breakdown else None,
        reason=record.get("reason", ""),
        targeted_seconds=record.get("targeted_seconds", 0.0),
        full_suite_seconds=record.get("full_suite_seconds", 0.0),
        full_suite_ran=record.get("full_suite_ran", False),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    parser.add_argument("venv_python")
    parser.add_argument("--selection", type=Path, default=None)
    parser.add_argument("--count", type=int, default=40,
                        help="stratified sample size across the score range")
    parser.add_argument("--all", action="store_true", dest="package_all",
                        help="package every admitted challenge, ignoring --count")
    parser.add_argument("--output-dir", type=Path, default=Path("phase5_output"))
    args = parser.parse_args()
    repo_dir = args.repo_dir.resolve()
    python = str(Path(args.venv_python).resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)

    watch = Stopwatch()
    baseline_cache_hit = is_cached(repo_dir)
    with watch.stage("baseline"):
        baseline = compute_baseline(repo_dir, args.package, python)
    print(f"baseline: {baseline.total_tests} tests "
          f"({'cache hit' if baseline_cache_hit else 'computed'})")

    if args.selection:
        payload = json.loads(args.selection.read_text(encoding="utf-8"))
        records = payload["results"]
        counts = payload.get("counts", {})
    else:
        records, counts = _run_selection(repo_dir, args.package, python, baseline, watch)
        (args.output_dir / "selection.json").write_text(
            json.dumps({"commit_sha": baseline.commit_sha, "counts": counts, "results": records}, indent=2),
            encoding="utf-8",
        )

    all_results = [_classification(r) for r in records]

    taxonomy = Counter(r["outcome"] for r in records)
    admitted = [r for r in records if r["outcome"] == Outcome.ADMITTED]
    chosen = select_challenges(admitted, None if args.package_all else args.count)
    spread = Counter(bucket_of(r["score_breakdown"]["score"]) for r in chosen)
    print(f"taxonomy: {dict(taxonomy)}")
    print(f"admitted: {len(admitted)}; describing and packaging {len(chosen)}"
          f" ({'all' if args.package_all else 'stratified'})")
    print("buckets: " + " · ".join(f"{n} {spread[n]}" for n in BUCKET_NAMES))
    print(f"{describe_module.DISABLE_ENV}={'set' if describe_module.bedrock_disabled() else 'unset'}\n")

    # Describe everything first, then disambiguate titles across the batch, then
    # package -- the packaged copy has to match what the grid shows.
    prepared = []
    for record in chosen:
        result = _classification(record)
        site = result.site
        source = (repo_dir / site.path).read_text(encoding="utf-8")
        inp = describe_module.build_input(site, source, result.representative_test, result.traceback)
        copy = describe_module.describe(inp)
        prepared.append((record, result, site, source, copy))

    titles = describe_module.disambiguate_titles(
        [c.title for *_, c in prepared],
        [site.lineno for _, _, site, _, _ in prepared],
    )

    rows = []
    packaging_started = time.perf_counter()
    for (record, result, site, source, copy), title in zip(prepared, titles):
        cid = challenge_id(repo_dir.name, baseline.commit_sha, site.path, site.lineno,
                           site.operator_id, site.mutated_token)
        package_challenge(
            repo_dir=repo_dir,
            repo_name=repo_dir.name,
            commit_sha=baseline.commit_sha,
            site=site,
            original_source=source,
            mutated_source=adapter.apply(source, site),
            classification=result,
            output_dir=args.output_dir / cid,
            title=title,
            description=copy.description,
        )
        sb = result.score_breakdown
        rows.append(
            {
                "challenge_id": cid,
                "title": title,
                "description": copy.description,
                "description_source": copy.source,
                "description_rejections": copy.rejections,
                "file_path": site.path,
                "lineno": site.lineno,
                "enclosing_function": site.enclosing_function_name,
                "operator": site.operator_id,
                "mutation": f"{site.original_token!r} -> {site.mutated_token!r}",
                "failing_test": result.representative_test,
                "difficulty_score": round(sb.score, 2),
                # The three difficulty bars on the card are these, not a
                # recomputation: the web app must not have to re-derive them.
                "score_breakdown": {
                    "displacement": sb.displacement,
                    "search_space": sb.search_space,
                    "noise": round(sb.noise, 6),
                    "name_leak": sb.name_leak,
                    "d": round(sb.d, 4),
                    "s": round(sb.s, 4),
                    "n": round(sb.n, 4),
                    "score": round(sb.score, 4),
                },
            }
        )
    watch.record("packaging", time.perf_counter() - packaging_started)

    (args.output_dir / "challenges.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    report = build_run_report(
        repo=repo_dir.name,
        commit_sha=baseline.commit_sha,
        results=all_results,
        candidates_generated=counts.get("candidates_generated", 0),
        candidates_on_covered_lines=counts.get("candidates_on_covered_lines", len(all_results)),
        stopwatch=watch,
        baseline_cache_hit=baseline_cache_hit,
        baseline_total_tests=baseline.total_tests,
    )
    report_path = write_run_report(report, args.output_dir)

    for i, row in enumerate(rows, 1):
        print(f"{i:>2}. [{row['description_source']}] {row['title']}")
        print(f"    {row['description']}")
        print(f"    ({row['file_path']}:{row['lineno']} {row['operator']} {row['mutation']}, "
              f"score {row['difficulty_score']})")
    sources = Counter(r["description_source"] for r in rows)
    print(f"\n{len(rows)} challenges packaged under {args.output_dir}/ -- {dict(sources)}")

    print("\n=== REJECTION TAXONOMY ===")
    print(format_taxonomy(report))
    print(f"\nrun report: {report_path}")


if __name__ == "__main__":
    main()
