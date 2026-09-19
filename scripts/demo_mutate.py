"""
Phase 2 deliverable: run find_candidates over a real repo's source tree,
print N generated mutations as one-line diffs, and show the full diff of one
mutated file.

Usage:
    python scripts/demo_mutate.py <repo_dir> <package_name> [--count N]
    python scripts/demo_mutate.py <repo_dir> <module_path> --language go

`package_name` is the import package for Python ("tenacity") and the go.mod
module path for Go ("github.com/golang-jwt/jwt/v5") -- the adapter decides
what it means, via source_root().
"""
from __future__ import annotations

import argparse
import difflib
from pathlib import Path

from bugforge.languages import DEFAULT_LANGUAGE, available_languages, get_adapter


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--language", default=DEFAULT_LANGUAGE, choices=available_languages())
    args = parser.parse_args()

    adapter = get_adapter(args.language)
    root = adapter.source_root(args.repo_dir, args.package)
    all_sites = []  # (rel_path, source, site)
    for source_file in adapter.discover_sources(root):
        rel = str(source_file.relative_to(args.repo_dir)).replace("\\", "/")
        source = source_file.read_text(encoding="utf-8")
        try:
            sites = adapter.find_candidates(source, rel)
        except SyntaxError:
            continue
        for site in sites:
            all_sites.append((rel, source, site))

    print(f"total candidates found: {len(all_sites)}\n")

    shown = all_sites[: args.count]
    for rel, source, site in shown:
        mutated = adapter.apply(source, site)
        before_line = source.splitlines()[site.lineno - 1]
        after_line = mutated.splitlines()[site.lineno - 1]
        print(f"{rel}:{site.lineno} [{site.operator_id}] {site.original_token!r} -> {site.mutated_token!r}")
        print(f"  - {before_line.strip()}")
        print(f"  + {after_line.strip()}")

    if all_sites:
        rel, source, site = all_sites[0]
        mutated = adapter.apply(source, site)
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
