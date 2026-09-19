"""
Phase 3: targeted test execution for a single mutation.

Every run happens on a throwaway copy of the repo tree in a temp dir --
the cached clone in ./cache is never written to. Given a MutationSite and
its already-applied mutated source, this runs either a specific list of
test ids (the ones the baseline says cover that line) or the full suite,
under a hard timeout.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bugforge.models import MutationSite

DEFAULT_TIMEOUT_S = 30

# Build and test detritus that must never be copied into a mutated tree. The
# union across languages rather than one list per language: a pattern that
# matches nothing in a Python repo costs nothing, and a single list means
# there is one place to look when a stale artifact survives a copy.
BUILD_ARTIFACTS = (
    ".coverage",
    ".pytest_cache",
    "__pycache__",
    "*.pyc",
    # Go: compiled test binaries and coverage profiles land beside the source.
    "*.test",
    "*.exe",
    "*.out",
)

# Mutated trees get a fresh history at package time, so .git is dead weight
# here. cloud/workspace.py copies the repo WITH .git, because compute_baseline
# reads HEAD to pin the commit sha -- which is why the two lists are named
# separately instead of one being a slice of the other.
COPY_IGNORE = (".git", *BUILD_ARTIFACTS)

_FAILED_RE = re.compile(r"^(?:FAILED|ERROR) (\S+)", re.MULTILINE)
_PASSED_COUNT_RE = re.compile(r"(\d+) passed")
_FAILED_COUNT_RE = re.compile(r"(\d+) failed")
_ERROR_COUNT_RE = re.compile(r"(\d+) error")


@dataclass
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    passed: int
    failed: int
    errors: int
    timed_out: bool
    collection_error: bool
    failing_tests: list[str] = field(default_factory=list)

    @property
    def num_failed_or_errored(self) -> int:
        return self.failed + self.errors


def _parse_pytest_output(output: str) -> tuple[int, int, int, list[str]]:
    passed_m = _PASSED_COUNT_RE.search(output)
    failed_m = _FAILED_COUNT_RE.search(output)
    error_m = _ERROR_COUNT_RE.search(output)
    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else 0
    errors = int(error_m.group(1)) if error_m else 0
    failing_tests = _FAILED_RE.findall(output)
    return passed, failed, errors, failing_tests


def run_pytest(
    tree_dir: Path, python: str, test_ids: list[str] | None = None, timeout: int = DEFAULT_TIMEOUT_S
) -> RunResult:
    """Runs pytest inside tree_dir. test_ids=None (or []) runs the full suite."""
    cmd = [python, "-m", "pytest", "-q", "--tb=long", "--no-header"]
    if test_ids:
        cmd.extend(test_ids)

    try:
        proc = subprocess.run(cmd, cwd=tree_dir, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        return RunResult(
            returncode=-1,
            stdout=e.stdout or "",
            stderr=e.stderr or "",
            passed=0,
            failed=0,
            errors=0,
            timed_out=True,
            collection_error=False,
            failing_tests=[],
        )

    output = proc.stdout + "\n" + proc.stderr
    passed, failed, errors, failing_tests = _parse_pytest_output(output)
    # A collection/import error (missing module, syntax error introduced by a
    # bad mutation, etc.) shows up as pytest reporting errors with nothing
    # passed or failed -- regardless of the exact process exit code.
    collection_error = errors > 0 and passed == 0 and failed == 0

    return RunResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        passed=passed,
        failed=failed,
        errors=errors,
        timed_out=False,
        collection_error=collection_error,
        failing_tests=failing_tests,
    )


def materialize_mutated_tree(repo_dir: Path, dest_dir: Path, site: MutationSite, mutated_source: str) -> None:
    """Copies repo_dir into dest_dir (skipping .git) and overwrites the one
    mutated file. repo_dir itself is never modified."""
    shutil.copytree(
        repo_dir,
        dest_dir,
        ignore=shutil.ignore_patterns(*COPY_IGNORE),
    )
    target = dest_dir / site.path
    target.write_text(mutated_source, encoding="utf-8")


def run_mutation(
    repo_dir: Path,
    python: str,
    site: MutationSite,
    mutated_source: str,
    test_ids: list[str] | None,
    timeout: int = DEFAULT_TIMEOUT_S,
) -> RunResult:
    """Materializes the mutation onto a fresh copy of repo_dir in a temp dir,
    runs `test_ids` (or the full suite if falsy) against it, then discards
    the copy. Never touches repo_dir."""
    with tempfile.TemporaryDirectory(prefix="bugforge-mutation-") as tmp:
        tree_dir = Path(tmp) / "tree"
        materialize_mutated_tree(repo_dir, tree_dir, site, mutated_source)
        return run_pytest(tree_dir, python, test_ids, timeout)


def run_mutation_with(
    adapter,
    repo_dir: Path,
    site: MutationSite,
    mutated_source: str,
    test_ids: list[str] | None,
    runner,
) -> RunResult:
    """run_mutation(), but the adapter decides what "run the tests" means.

    Identical in shape to run_mutation: materialize the mutation onto a fresh
    copy of repo_dir, run `test_ids` (or the full suite if falsy) against it,
    discard the copy. The difference is that the command is the language's,
    not pytest's, so a Go repo compiles and runs `go test` here while a Python
    repo runs exactly what it ran before.

    `runner` is a RunnerConfig; it carries the timeout, so unlike run_mutation
    there is no separate timeout argument to disagree with it.
    """
    with tempfile.TemporaryDirectory(prefix="bugforge-mutation-") as tmp:
        tree_dir = Path(tmp) / "tree"
        materialize_mutated_tree(repo_dir, tree_dir, site, mutated_source)
        return adapter.run_tests(tree_dir, test_ids, runner)
