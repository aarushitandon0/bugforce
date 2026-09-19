"""
Phase 3 deliverable: run the whole pipeline (mutate -> run -> classify ->
score) against one repo. Prints one admitted challenge with its real
traceback and score breakdown, the rejection taxonomy, and the test-gap list.

Usage:
    python scripts/demo_pipeline.py <repo_dir> <package> <venv_python> [--limit N]
    python scripts/demo_pipeline.py <repo_dir> <module_path> --language go

`venv_python` is the interpreter the repo's dependencies are installed into,
and is only meaningful for Python repos -- `go test` needs no equivalent.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from bugforge.baseline import is_cached
from bugforge.languages import DEFAULT_LANGUAGE, available_languages, get_adapter
from bugforge.models import RunnerConfig
from bugforge.mutate import MutationError
from bugforge.package import package_challenge
from bugforge.run_report import Stopwatch, build_run_report, format_taxonomy, write_run_report
from bugforge.select import Outcome, run_selection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    # Optional: only the Python adapter has anything to do with an
    # interpreter. A Go repo's tests are run by `go test`.
    parser.add_argument("venv_python", nargs="?", default=sys.executable)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, choices=available_languages())
    parser.add_argument("--limit", type=int, default=None, help="cap number of candidates for a faster demo run")
    parser.add_argument("--output-dir", type=Path, default=Path("phase3_output"))
    args = parser.parse_args()
    args.repo_dir = args.repo_dir.resolve()
    args.venv_python = str(Path(args.venv_python).resolve())

    adapter = get_adapter(args.language)
    runner = RunnerConfig(package=args.package, python=args.venv_python)

    watch = Stopwatch()

    print("loading baseline...")
    baseline_cache_hit = is_cached(args.repo_dir)
    with watch.stage("baseline"):
        baseline = adapter.baseline(args.repo_dir, runner)
    print(f"baseline: {baseline.total_tests} tests, {baseline.covered_line_count()} covered lines "
          f"({'cache hit' if baseline_cache_hit else 'computed'})\n")

    pkg_dir = adapter.source_root(args.repo_dir, args.package)
    sites_and_sources = []
    sources_by_file = {}
    candidates_generated = 0
    generate_started = time.perf_counter()
    for source_file in adapter.discover_sources(pkg_dir):
        rel = str(source_file.relative_to(args.repo_dir)).replace("\\", "/")
        source = source_file.read_text(encoding="utf-8")
        sources_by_file[rel] = source
        try:
            sites = adapter.find_candidates(source, rel)
        except SyntaxError:
            continue
        # only sites on covered lines make it into the pipeline at all
        candidates_generated += len(sites)
        for site in sites:
            if not baseline.tests_for_line(site.path, site.lineno):
                continue
            try:
                mutated = adapter.apply(source, site)
            except MutationError:
                continue
            sites_and_sources.append((site, mutated))

    watch.record("generate_candidates", time.perf_counter() - generate_started)
    candidates_on_covered_lines = len(sites_and_sources)

    if args.limit:
        sites_and_sources = sites_and_sources[: args.limit]

    print(f"running {len(sites_and_sources)} covered candidates through the pipeline...\n")
    with watch.stage("selection_total"):
        results, taxonomy = run_selection(args.repo_dir, adapter, runner, baseline, sites_and_sources)

    # (b) rejection taxonomy -- written to disk as well as printed, because
    # the blog post and the rejection slide are downstream of these counts.
    report = build_run_report(
        repo=args.repo_dir.name,
        commit_sha=baseline.commit_sha,
        results=results,
        candidates_generated=candidates_generated,
        candidates_on_covered_lines=candidates_on_covered_lines,
        stopwatch=watch,
        baseline_cache_hit=baseline_cache_hit,
        baseline_total_tests=baseline.total_tests,
    )
    report_path = write_run_report(report, args.output_dir)
    print("=== REJECTION TAXONOMY ===")
    print(format_taxonomy(report))
    print()

    # (c) test-gap list
    gaps = [r for r in results if r.outcome == Outcome.TEST_GAP]
    print(f"=== TEST GAPS ({len(gaps)}) ===")
    for r in gaps:
        print(f"  {r.site.path}:{r.site.lineno} [{r.site.operator_id}] {r.site.original_token!r} -> {r.site.mutated_token!r}")
    print()

    # (a) one admitted challenge
    admitted = [r for r in results if r.outcome == Outcome.ADMITTED]
    admitted.sort(key=lambda r: -r.score_breakdown.score)
    print(f"=== ADMITTED CHALLENGES: {len(admitted)} ===\n")
    if not admitted:
        print("(none admitted in this run)")
        print(f"run report: {report_path}")
        return

    best = admitted[0]
    sb = best.score_breakdown
    print(f"--- {best.site.path}:{best.site.lineno} [{best.site.operator_id}] ---")
    print(f"mutation: {best.site.original_token!r} -> {best.site.mutated_token!r}")
    print(f"representative failing test: {best.representative_test}")
    print(f"failing tests ({len(best.failing_tests)}/{best.total_tests}): {best.failing_tests}")
    print("\nscore breakdown:")
    print(f"  displacement = {sb.displacement}  (d = {sb.d:.3f})")
    print(f"  search_space = {sb.search_space}  (s = {sb.s:.3f})")
    print(f"  noise        = {sb.noise:.4f}  (n = {sb.n:.3f})")
    print(f"  name_leak    = {sb.name_leak}")
    print(f"  SCORE        = {sb.score:.2f}")
    print("\ncaptured traceback:")
    print(best.traceback)

    print("\npackaging this challenge...")
    original_source = sources_by_file[best.site.path]
    packaging_started = time.perf_counter()
    challenge = package_challenge(
        repo_dir=args.repo_dir,
        repo_name=args.repo_dir.name,
        commit_sha=baseline.commit_sha,
        site=best.site,
        original_source=original_source,
        mutated_source=adapter.apply(original_source, best.site),
        classification=best,
        output_dir=args.output_dir,
    )
    print(json.dumps(__import__("dataclasses").asdict(challenge), indent=2))
    watch.record("packaging", time.perf_counter() - packaging_started)
    report["timings_seconds"]["packaging"] = watch.stages["packaging"]
    write_run_report(report, args.output_dir)
    print(f"\ntarballs + JSON written under {args.output_dir}/")
    print(f"run report: {report_path}")


if __name__ == "__main__":
    main()
