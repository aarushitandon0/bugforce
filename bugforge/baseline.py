"""
Phase 1: baseline runner.

Runs a repo's full test suite once with coverage contexts enabled, then reads
the resulting .coverage SQLite database directly to build a line -> tests map.
That map tells us, for every line, which tests actually execute it -- which is
what lets Phase 3 run only the relevant tests per mutation instead of the full
suite.

Caches the result as JSON keyed on (repo, commit_sha) so it's never
recomputed for the same commit.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from coverage import CoverageData

from bugforge.models import Baseline

CACHE_DIR = Path(__file__).resolve().parent.parent / ".baseline_cache"


class BaselineError(RuntimeError):
    """Raised when the repo's suite isn't green or coverage contexts are empty."""


def _cache_path(repo: str, commit_sha: str) -> Path:
    safe_repo = repo.replace("/", "__")
    return CACHE_DIR / f"{safe_repo}__{commit_sha}.json"


def _git_head_sha(repo_dir: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise BaselineError(f"could not read HEAD sha: {proc.stderr.strip()}")
    return proc.stdout.strip()


def _run_pytest_with_coverage(repo_dir: Path, package: str, python: str) -> tuple[bool, int]:
    """Runs the suite once with per-test coverage contexts. Returns (green, total_tests)."""
    cov_file = repo_dir / ".coverage"
    if cov_file.exists():
        cov_file.unlink()

    proc = subprocess.run(
        [
            python,
            "-m",
            "pytest",
            f"--cov={package}",
            "--cov-context=test",
            "-q",
            "--no-header",
        ],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=600,
    )
    output = proc.stdout + "\n" + proc.stderr

    m = re.search(r"(\d+) passed", output)
    passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", output)
    errors = int(m.group(1)) if m else 0

    green = proc.returncode == 0 and failed == 0 and errors == 0
    if not green:
        raise BaselineError(
            f"suite not green: passed={passed} failed={failed} errors={errors}\n"
            f"{output[-2000:]}"
        )
    return green, passed


def _build_line_to_tests(repo_dir: Path) -> dict[str, list[str]]:
    """Reads .coverage directly and builds {file:lineno -> [test_id, ...]}."""
    cov_file = repo_dir / ".coverage"
    if not cov_file.exists():
        raise BaselineError(f"no .coverage file produced at {cov_file}")

    data = CoverageData(basename=str(cov_file))
    data.read()

    measured_files = list(data.measured_files())
    if not measured_files:
        raise BaselineError("coverage recorded no measured files")

    # Coverage contexts must have actually been populated by --cov-context=test.
    # If contexts() is empty (or only the synthetic "" context exists), the
    # flag silently didn't take effect and everything downstream is bogus --
    # fail loudly instead of returning an empty map.
    all_contexts = data.measured_contexts()
    real_contexts = {c for c in all_contexts if c}
    if not real_contexts:
        raise BaselineError(
            "coverage contexts are empty -- --cov-context=test did not take "
            f"effect (measured_contexts()={all_contexts!r})"
        )

    line_to_tests: dict[str, list[str]] = {}
    for file_path in measured_files:
        try:
            rel = str(Path(file_path).resolve().relative_to(repo_dir.resolve()))
        except ValueError:
            rel = file_path
        rel = rel.replace("\\", "/")

        contexts_by_line = data.contexts_by_lineno(file_path)
        for lineno, contexts in contexts_by_line.items():
            # pytest-cov contexts are "<nodeid>|run" (or |setup, |teardown,
            # neither of which --cov-context=test attributes lines to); strip
            # the suffix so downstream consumers get a plain pytest nodeid.
            test_ids = sorted({c.split("|", 1)[0] for c in contexts if c})
            if test_ids:
                line_to_tests[f"{rel}:{lineno}"] = test_ids

    if not line_to_tests:
        raise BaselineError("line_to_tests map is empty after reading coverage db")

    return line_to_tests


# Public aliases. A language adapter that builds its line->tests map some
# other way (Go runs one coverage profile per test; see languages/go.py) still
# caches it here, under the same `(repo, commit_sha)` key and in the same
# directory, so there is one answer to "has this commit been baselined".
cache_path = _cache_path
git_head_sha = _git_head_sha


def is_cached(repo_dir: Path) -> bool:
    """True if compute_baseline() would hit the cache for this repo at HEAD.

    Callers that want to report whether the slow step actually ran have to ask
    before calling, because compute_baseline() returns the same Baseline either
    way.
    """
    repo_dir = repo_dir.resolve()
    return _cache_path(repo_dir.name, _git_head_sha(repo_dir)).exists()


def compute_baseline(repo_dir: Path, package: str, python: str, use_cache: bool = True) -> Baseline:
    """Runs the full suite with coverage contexts and builds the baseline.

    Raises BaselineError if the suite isn't green or contexts didn't populate.
    """
    repo_dir = repo_dir.resolve()
    commit_sha = _git_head_sha(repo_dir)
    repo_name = repo_dir.name

    cache_path = _cache_path(repo_name, commit_sha)
    if use_cache and cache_path.exists():
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        return Baseline(**payload)

    green, total_tests = _run_pytest_with_coverage(repo_dir, package, python)
    line_to_tests = _build_line_to_tests(repo_dir)

    baseline = Baseline(
        repo=repo_name,
        commit_sha=commit_sha,
        total_tests=total_tests,
        green=green,
        line_to_tests=line_to_tests,
    )

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(asdict(baseline), indent=2), encoding="utf-8")

    return baseline


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("repo_dir", type=Path)
    parser.add_argument("package")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument("--spot-check", nargs="*", default=[], help="file:lineno entries to print coverage for")
    args = parser.parse_args()

    baseline = compute_baseline(args.repo_dir, args.package, args.python, use_cache=not args.no_cache)

    print(f"repo: {baseline.repo}  commit: {baseline.commit_sha}")
    print(f"total tests: {baseline.total_tests}")
    print(f"green: {baseline.green}")
    print(f"covered lines: {baseline.covered_line_count()}")

    for entry in args.spot_check:
        file_path, lineno = entry.rsplit(":", 1)
        tests = baseline.tests_for_line(file_path, int(lineno))
        print(f"{entry}: {len(tests)} tests -> {tests}")


if __name__ == "__main__":
    main()
