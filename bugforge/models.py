"""Shared dataclasses for the BugForge mutation pipeline."""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

# "path/to/file.py:lineno" -> sorted test ids that execute that line. Named
# because it is the one piece of data every language adapter has to produce,
# and the one that is hardest to produce outside Python (see LanguageAdapter).
LineToTests = dict[str, list[str]]


@dataclass(frozen=True)
class RunnerConfig:
    """How to invoke a repo's test suite. Passed to LanguageAdapter methods
    that shell out, so the adapter itself stays stateless about a given repo."""

    package: str = ""
    python: str = sys.executable
    timeout_s: int = 30
    use_cache: bool = True


@dataclass
class Baseline:
    """Result of running a repo's full test suite once with coverage contexts."""

    repo: str
    commit_sha: str
    total_tests: int
    green: bool
    # keys are "path/to/file.py:lineno" (str, so it round-trips through JSON)
    # values are sorted lists of test ids that execute that line.
    line_to_tests: dict[str, list[str]] = field(default_factory=dict)

    def tests_for_line(self, file_path: str, lineno: int) -> list[str]:
        return self.line_to_tests.get(f"{file_path}:{lineno}", [])

    def covered_line_count(self) -> int:
        return len(self.line_to_tests)


@dataclass
class MutationSite:
    """A single located mutation, before it has been applied or graded."""

    path: str
    lineno: int
    col_start: int  # UTF-8 byte offset into the line
    col_end: int  # UTF-8 byte offset into the line
    operator_id: str
    original_token: str
    mutated_token: str
    enclosing_function_name: str | None
    # Title copy only. A dunder ("__call__") names nothing a learner can
    # recognise, and tenacity puts most of its logic in __call__ methods of
    # differently-named classes, so the class is the distinguishing part.
    enclosing_class_name: str | None = None


@dataclass
class Candidate:
    """A single located, applicable mutation before it's been graded."""

    file_path: str
    lineno: int
    col_offset: int
    end_col_offset: int
    original_text: str
    mutated_text: str
    mutator_name: str
    node_type: str
    covering_tests: list[str] = field(default_factory=list)


@dataclass
class Challenge:
    """A graded mutation promoted to a learner-facing debugging challenge."""

    id: str
    repo: str
    commit_sha: str
    file_path: str
    lineno: int
    mutator_name: str
    diff: str
    failing_tests: list[str]
    difficulty_score: float
    title: str = ""
    description: str = ""
