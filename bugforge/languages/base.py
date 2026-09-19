"""
The language seam.

Everything BugForge does to a repo is one of eight operations, and all eight are
language-specific. `LanguageAdapter` names them; `PythonAdapter` and
`GoAdapter` implement them. Nothing in phases 1-3 is generic by accident --
this protocol is the list of what a new language has to supply.

The hard one is the coverage map. Python gives us per-test coverage contexts
for free (`--cov-context=test`), and the whole `line -> tests that cover it`
map falls out of one suite run. Go has no such thing: `GoAdapter` runs one
coverage profile per test and unions them, which is O(tests) suite runs and
only survives because the result is cached per `(repo, commit_sha)`. Assume a
new language costs the same unless proven otherwise; see FEATURES.md,
"Adding a language".
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from bugforge.models import Baseline, LineToTests, MutationSite, RunnerConfig
from bugforge.runner import RunResult


class UnsupportedLanguageError(RuntimeError):
    """Raised when no adapter is registered for a requested language."""


@runtime_checkable
class LanguageAdapter(Protocol):
    """One language's answer to the five questions the pipeline asks a repo."""

    name: str

    def source_root(self, tree: Path, package: str) -> Path:
        """Where under `tree` this language's mutable source begins.

        Python installs a package and pytest shadows it from the repo root, so
        the root is the package directory and `package` is a directory name.
        Go has no such thing: `package` is the module path from go.mod, which
        is not a path on disk at all, and source lives throughout the tree.
        """
        ...

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

    def baseline(self, repo: Path, runner: RunnerConfig) -> Baseline:
        """The commit sha, the test count, and the `file:lineno -> [test ids]` map.

        Must raise rather than return a Baseline if the suite is not already
        green: a red baseline makes every later verdict meaningless.

        This is the expensive call. Implementations are expected to cache on
        `(repo, commit_sha)` -- `runner.use_cache` says whether they may.
        """
        ...

    def coverage_map(self, repo: Path, runner: RunnerConfig) -> LineToTests:
        """Just the map half of `baseline`, for callers that need nothing else."""
        ...

    def run_tests(self, tree: Path, test_ids: list[str] | None, runner: RunnerConfig) -> RunResult:
        """Run `test_ids` (or the whole suite when falsy) inside `tree`."""
        ...

    def extract_failure(self, output: str, test_id: str) -> tuple[list[tuple[str, int, str]], str]:
        """Pull one failing test's stack out of a full-suite run's output.

        Returns `(frames, raw text)`. Frames are `(file, lineno, function)` in
        call order -- outermost first, the frame that failed last -- with
        repo-relative file paths, because Phase 3 measures `displacement` by
        comparing the deepest frame's file against the mutated one and the raw
        text is what the learner is handed as `traceback.txt`.

        Returning no frames is allowed but expensive: `displacement` then
        falls back to its maximum, which is a wrong score rather than a
        visible failure. A language whose failures carry no stack should say
        so here rather than let that fallback stand in for a measurement.
        """
        ...
