"""
The language seam.

Everything BugForge does to a repo is one of five operations, and all five are
language-specific. `LanguageAdapter` names them; `PythonAdapter` is currently
the only implementation. Nothing in phases 1-3 is generic by accident -- this
protocol is the list of what a second language would have to supply, written
down so the answer to "can you do Go?" is a work item and not a shrug.

The hard one is `coverage_map`. Python gives us per-test coverage contexts for
free (`--cov-context=test`), and the whole `line -> tests that cover it` map
falls out of one suite run. No other toolchain we have looked at does this;
see FEATURES.md, "Adding a language", for what each one would need instead.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from bugforge.models import LineToTests, MutationSite, RunnerConfig
from bugforge.runner import RunResult


class UnsupportedLanguageError(RuntimeError):
    """Raised when no adapter is registered for a requested language."""


@runtime_checkable
class LanguageAdapter(Protocol):
    """One language's answer to the five questions the pipeline asks a repo."""

    name: str

    def discover_sources(self, repo: Path) -> list[Path]:
        """Every file under `repo` that is eligible for mutation.

        Excludes tests, docs and packaging scripts -- mutating a test proves
        nothing, and a learner never sees those files as the defect site.
        """
        ...

    def find_candidates(self, source: str, path: str) -> list[MutationSite]:
        """Locate mutable tokens in one file. Positions only -- no rewriting.

        Offsets on the returned sites are byte offsets into a single line, so
        `apply` can splice without reformatting anything.
        """
        ...

    def apply(self, source: str, site: MutationSite) -> str:
        """Splice one located mutation into `source` and return the new text.

        Must change exactly one line and must leave every other byte alone.
        """
        ...

    def coverage_map(self, repo: Path, runner: RunnerConfig) -> LineToTests:
        """Run the suite once and return `file:lineno -> [test ids]`.

        Must refuse to return a map if the suite is not already green: a red
        baseline makes every later verdict meaningless.
        """
        ...

    def run_tests(self, tree: Path, test_ids: list[str] | None, runner: RunnerConfig) -> RunResult:
        """Run `test_ids` (or the whole suite when falsy) inside `tree`."""
        ...
