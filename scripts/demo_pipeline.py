"""
Phase 3 deliverable: run the whole pipeline (mutate -> run -> classify ->
score) against one repo. Prints one admitted challenge with its real
traceback and score breakdown, the rejection taxonomy, and the test-gap list.

Usage:
    python scripts/demo_pipeline.py <repo_dir> <package> <venv_python> [--limit N]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bugforge.baseline import compute_baseline
from bugforge.mutate import MutationError, apply, find_candidates
from bugforge.package import package_challenge
from bugforge.select import Outcome, run_selection


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    parser.add_argument("venv_python")
    parser.add_argument("--limit", type=int, default=None, help="cap number of candidates for a faster demo run")
    parser.add_argument("--output-dir", type=Path, default=Path("phase3_output"))
    args = parser.parse_args()
    args.repo_dir = args.repo_dir.resolve()
    args.venv_python = str(Path(args.venv_python).resolve())

    print("loading baseline...")
    baseline = compute_baseline(args.repo_dir, args.package, args.venv_python)
    print(f"baseline: {baseline.total_tests} tests, {baseline.covered_line_count()} covered lines\n")

    pkg_dir = args.repo_dir / args.package
    sites_and_sources = []
    sources_by_file = {}
    for py_file in sorted(pkg_dir.rglob("*.py")):
        rel = str(py_file.relative_to(args.repo_dir)).replace("\\", "/")
        source = py_file.read_text(encoding="utf-8")
        sources_by_file[rel] = source
        try:
            sites = find_candidates(source, rel)
        except SyntaxError:
            continue
        # only sites on covered lines make it into the pipeline at all
        for site in sites:
            if not baseline.tests_for_line(site.path, site.lineno):
                continue
            try:
                mutated = apply(source, site)
            except MutationError:
                continue
            sites_and_sources.append((site, mutated))

    if args.limit:
        sites_and_sources = sites_and_sources[: args.limit]

    print(f"running {len(sites_and_sources)} covered candidates through the pipeline...\n")
    results, taxonomy = run_selection(args.repo_dir, args.venv_python, baseline, sites_and_sources)

    # (b) rejection taxonomy
    print("=== REJECTION TAXONOMY ===")
    total = sum(taxonomy.values())
    for outcome in sorted(taxonomy, key=lambda k: -taxonomy[k]):
        print(f"  {outcome:<22} {taxonomy[outcome]:>4}  ({taxonomy[outcome] / total:.0%})")
    print(f"  {'TOTAL':<22} {total:>4}\n")

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
    challenge = package_challenge(
        repo_dir=args.repo_dir,
        repo_name=args.repo_dir.name,
        commit_sha=baseline.commit_sha,
        site=best.site,
        original_source=original_source,
        mutated_source=apply(original_source, best.site),
        classification=best,
        output_dir=args.output_dir,
    )
    print(json.dumps(__import__("dataclasses").asdict(challenge), indent=2))
    print(f"\ntarballs + JSON written under {args.output_dir}/")


if __name__ == "__main__":
    main()
