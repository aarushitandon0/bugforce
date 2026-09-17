"""
Phase 5 deliverable: take the top-N admitted challenges for one repo, write a
title + description for each, and package them with that copy.

Usage:
    python scripts/demo_describe.py <repo_dir> <package> <venv_python>
        [--selection results.json] [--count 20] [--output-dir phase5_output]

--selection reuses a saved run_selection result (the slow step). Without it,
the selection runs here and is saved to <output-dir>/selection.json.
Set BUGFORGE_DISABLE_BEDROCK=1 to force the template for every challenge.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from bugforge.baseline import compute_baseline
from bugforge.models import MutationSite
from bugforge.mutate import MutationError, apply, find_candidates
from bugforge.package import package_challenge
from bugforge.select import ClassificationResult, Outcome, ScoreBreakdown, run_selection
from cloud import describe as describe_module
from cloud.ids import challenge_id


def _run_selection(repo_dir: Path, package: str, python: str, baseline) -> list[dict]:
    pairs = []
    for py_file in sorted((repo_dir / package).rglob("*.py")):
        rel = str(py_file.relative_to(repo_dir)).replace("\\", "/")
        source = py_file.read_text(encoding="utf-8")
        for site in find_candidates(source, rel):
            if not baseline.tests_for_line(site.path, site.lineno):
                continue
            try:
                pairs.append((site, apply(source, site)))
            except MutationError:
                continue
    print(f"running {len(pairs)} covered candidates through selection...")
    results, _ = run_selection(repo_dir, python, baseline, pairs)
    return [
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
        }
        for r in results
    ]


def _classification(record: dict) -> ClassificationResult:
    return ClassificationResult(
        site=MutationSite(**record["site"]),
        outcome=record["outcome"],
        covering_tests=record["covering_tests"],
        failing_tests=record["failing_tests"],
        total_tests=record["total_tests"],
        traceback=record["traceback"],
        representative_test=record["representative_test"],
        score_breakdown=ScoreBreakdown(**record["score_breakdown"]),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    parser.add_argument("venv_python")
    parser.add_argument("--selection", type=Path, default=None)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--output-dir", type=Path, default=Path("phase5_output"))
    args = parser.parse_args()
    repo_dir = args.repo_dir.resolve()
    python = str(Path(args.venv_python).resolve())
    args.output_dir.mkdir(parents=True, exist_ok=True)

    baseline = compute_baseline(repo_dir, args.package, python)

    if args.selection:
        records = json.loads(args.selection.read_text(encoding="utf-8"))["results"]
    else:
        records = _run_selection(repo_dir, args.package, python, baseline)
        (args.output_dir / "selection.json").write_text(
            json.dumps({"commit_sha": baseline.commit_sha, "results": records}, indent=2),
            encoding="utf-8",
        )

    taxonomy = Counter(r["outcome"] for r in records)
    admitted = [r for r in records if r["outcome"] == Outcome.ADMITTED]
    admitted.sort(key=lambda r: (-r["score_breakdown"]["score"], r["site"]["path"], r["site"]["lineno"]))
    chosen = admitted[: args.count]
    print(f"taxonomy: {dict(taxonomy)}")
    print(f"admitted: {len(admitted)}; describing and packaging {len(chosen)}")
    print(f"{describe_module.DISABLE_ENV}={'set' if describe_module.bedrock_disabled() else 'unset'}\n")

    rows = []
    for record in chosen:
        result = _classification(record)
        site = result.site
        source = (repo_dir / site.path).read_text(encoding="utf-8")
        inp = describe_module.build_input(site, source, result.representative_test, result.traceback)
        copy = describe_module.describe(inp)

        cid = challenge_id(repo_dir.name, baseline.commit_sha, site.path, site.lineno,
                           site.operator_id, site.mutated_token)
        package_challenge(
            repo_dir=repo_dir,
            repo_name=repo_dir.name,
            commit_sha=baseline.commit_sha,
            site=site,
            original_source=source,
            mutated_source=apply(source, site),
            classification=result,
            output_dir=args.output_dir / cid,
            title=copy.title,
            description=copy.description,
        )
        rows.append(
            {
                "challenge_id": cid,
                "title": copy.title,
                "description": copy.description,
                "description_source": copy.source,
                "description_rejections": copy.rejections,
                "file_path": site.path,
                "lineno": site.lineno,
                "operator": site.operator_id,
                "mutation": f"{site.original_token!r} -> {site.mutated_token!r}",
                "failing_test": result.representative_test,
                "difficulty_score": round(result.score_breakdown.score, 2),
            }
        )

    (args.output_dir / "challenges.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")

    for i, row in enumerate(rows, 1):
        print(f"{i:>2}. [{row['description_source']}] {row['title']}")
        print(f"    {row['description']}")
        print(f"    ({row['file_path']}:{row['lineno']} {row['operator']} {row['mutation']}, "
              f"score {row['difficulty_score']})")
    sources = Counter(r["description_source"] for r in rows)
    print(f"\n{len(rows)} challenges packaged under {args.output_dir}/ -- {dict(sources)}")


if __name__ == "__main__":
    main()
