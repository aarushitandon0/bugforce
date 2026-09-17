"""
Phase 2 deliverable: run find_candidates over a real repo's source tree,
print N generated mutations as one-line diffs, and show the full diff of one
mutated file.

Usage:
    python scripts/demo_mutate.py <repo_dir> <package_name> [--count N]
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

from bugforge.mutate import apply, find_candidates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    parser.add_argument("--count", type=int, default=20)
    args = parser.parse_args()

    pkg_dir = args.repo_dir / args.package
    all_sites = []  # (rel_path, source, site)
    for py_file in sorted(pkg_dir.rglob("*.py")):
        rel = str(py_file.relative_to(args.repo_dir)).replace("\\", "/")
        source = py_file.read_text(encoding="utf-8")
        try:
            sites = find_candidates(source, rel)
        except SyntaxError:
            continue
        for site in sites:
            all_sites.append((rel, source, site))

    print(f"total candidates found: {len(all_sites)}\n")

    shown = all_sites[: args.count]
    for rel, source, site in shown:
        mutated = apply(source, site)
        before_line = source.splitlines()[site.lineno - 1]
        after_line = mutated.splitlines()[site.lineno - 1]
        print(f"{rel}:{site.lineno} [{site.operator_id}] {site.original_token!r} -> {site.mutated_token!r}")
        print(f"  - {before_line.strip()}")
        print(f"  + {after_line.strip()}")

    if all_sites:
        rel, source, site = all_sites[0]
        mutated = apply(source, site)
        print(f"\n=== full diff for {rel} (mutation at line {site.lineno}) ===")
        diff = difflib.unified_diff(
            source.splitlines(keepends=True),
            mutated.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
        print("".join(diff))


if __name__ == "__main__":
    main()
