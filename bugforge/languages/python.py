"""
Python: the first (and currently only) LanguageAdapter.

This module owns no logic. Every method forwards to the phase 1-3 code that
already existed -- baseline.py, mutate.py, runner.py -- so routing a call
through the adapter and calling the function directly do exactly the same
thing. The point of the indirection is that the pipeline can name what it
needs from a language without naming Python.
"""
from __future__ import annotations

from pathlib import Path

from bugforge import baseline as _baseline
from bugforge import mutate as _mutate
from bugforge import runner as _runner
from bugforge.models import LineToTests, MutationSite, RunnerConfig

SOURCE_SUFFIX = ".py"

# Directories that are never worth walking into: build detritus, vendored
# environments, and VCS metadata. Not a security boundary -- vetting is
# (infra/docker/vetted_repos.json) -- just noise removal.
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    "build",
    "dist",
    "site-packages",
    "node_modules",
}


class PythonAdapter:
    """CPython source, pytest for tests, coverage.py contexts for the map."""

    name = "python"

    def discover_sources(self, repo: Path) -> list[Path]:
        repo = Path(repo)
        found = []
        for path in sorted(repo.rglob(f"*{SOURCE_SUFFIX}")):
            rel = path.relative_to(repo)
            if _SKIP_DIRS & set(rel.parts[:-1]):
                continue
            if not _mutate.is_mutable_source_path(str(rel).replace("\\", "/")):
                continue
            found.append(path)
        return found

    def find_candidates(self, source: str, path: str) -> list[MutationSite]:
        return _mutate.find_candidates(source, path)

    def apply(self, source: str, site: MutationSite) -> str:
        return _mutate.apply(source, site)

    def coverage_map(self, repo: Path, runner: RunnerConfig) -> LineToTests:
        # compute_baseline does the green check, the empty-contexts check and
        # the on-disk cache; the map is the part the protocol promises.
        result = _baseline.compute_baseline(
            Path(repo), runner.package, runner.python, use_cache=runner.use_cache
        )
        return result.line_to_tests

    def run_tests(
        self, tree: Path, test_ids: list[str] | None, runner: RunnerConfig
    ) -> _runner.RunResult:
        return _runner.run_pytest(Path(tree), runner.python, test_ids, runner.timeout_s)
