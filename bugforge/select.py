"""
Phase 3: classify each mutation candidate, score the survivors, and produce
a rejection taxonomy.

Pipeline per candidate:
    1. run only the tests the baseline says cover the mutated line
    2. timeout                                -> DROP "timeout"
    3. collection/import error                -> DROP "catastrophic"
    4. none of the covering tests failed       -> TEST_GAP (maintainer report)
    5. otherwise, run the FULL suite once (accurate failing/total + a clean
       traceback); if more than 25% of the full suite fails -> DROP "too_loud"
    6. otherwise score it; ADMIT only score >= 3.0, else DROP "low_score"

All scoring inputs are deterministic and derived from data we already have:
the baseline's line->tests map doubles as a test->files map for search_space
(no need to re-run coverage), and the traceback is parsed from the full-suite
run's own --tb=long output.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from bugforge.models import Baseline, MutationSite
from bugforge.runner import RunResult, run_mutation

TIMEOUT_S = 30
TOO_LOUD_FRACTION = 0.25
ADMIT_THRESHOLD = 3.0

# A frame's location line. pytest's --tb=long prints it at the END of each
# frame's source excerpt as "path.py:123: " or, for the deepest frame,
# "path.py:123: AssertionError"; "path.py:123: in func" is accepted too. On
# Windows the path uses backslashes. The trailing group is informational only.
_FRAME_RE = re.compile(r"^(\S+\.py):(\d+):(?: in (\S+)| ?(\S*))$", re.MULTILINE)
_FAILURE_BLOCK_RE = re.compile(r"^=+ FAILURES =+\n(.*?)(?:^=+ short test summary|\Z)", re.MULTILINE | re.DOTALL)
_TEST_BLOCK_HEADER_RE = re.compile(r"^_{3,} (\S+) _{3,}$", re.MULTILINE)


class Outcome:
    ADMITTED = "ADMITTED"
    TEST_GAP = "TEST_GAP"
    DROP_CATASTROPHIC = "DROP_catastrophic"
    DROP_TIMEOUT = "DROP_timeout"
    DROP_TOO_LOUD = "DROP_too_loud"
    DROP_LOW_SCORE = "DROP_low_score"


@dataclass
class ScoreBreakdown:
    displacement: int
    search_space: int
    noise: float
    name_leak: bool
    d: float
    s: float
    n: float
    score: float


@dataclass
class ClassificationResult:
    site: MutationSite
    outcome: str
    covering_tests: list[str] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)
    total_tests: int = 0
    traceback: str = ""
    representative_test: str | None = None
    score_breakdown: ScoreBreakdown | None = None
    reason: str = ""


def _clamp(lo: float, hi: float, x: float) -> float:
    return max(lo, min(hi, x))


def _build_test_to_files(baseline: Baseline) -> dict[str, set[str]]:
    """Inverts baseline.line_to_tests into test_id -> {files it covers}.

    This IS the coverage context data (already captured once in Phase 1) --
    no need to re-run coverage during classification.
    """
    test_to_files: dict[str, set[str]] = {}
    for key, test_ids in baseline.line_to_tests.items():
        file_path = key.rsplit(":", 1)[0]
        for test_id in test_ids:
            test_to_files.setdefault(test_id, set()).add(file_path)
    return test_to_files


def _extract_traceback_frames(full_output: str, test_node_id: str) -> tuple[list[tuple[str, int, str]], str]:
    """Returns (ordered frame list, raw traceback text) for one failing test.

    Frames are (file, lineno, function), in call order (outermost first,
    deepest/failing frame last) as pytest's --tb=long prints them.
    """
    match = _FAILURE_BLOCK_RE.search(full_output)
    if not match:
        return [], ""
    failures_text = match.group(1)

    # split into per-test blocks on the "____ test_name ____" headers
    headers = list(_TEST_BLOCK_HEADER_RE.finditer(failures_text))
    short_name = test_node_id.split("::")[-1]
    block_text = ""
    for i, h in enumerate(headers):
        if h.group(1) == short_name or short_name in h.group(1):
            start = h.end()
            end = headers[i + 1].start() if i + 1 < len(headers) else len(failures_text)
            block_text = failures_text[start:end]
            break
    if not block_text:
        block_text = failures_text  # fall back to the whole section

    frames = [(f, int(ln), fn or tail) for f, ln, fn, tail in _FRAME_RE.findall(block_text)]
    return frames, block_text.strip()


def _displacement(mutated_file: str, frames: list[tuple[str, int, str]]) -> int:
    if not frames:
        return 4
    files = [f.replace("\\", "/") for f, _, _ in frames]
    mutated_norm = mutated_file.replace("\\", "/")
    if files[-1] == mutated_norm:
        return 0
    last_idx = None
    for i, f in enumerate(files):
        if f == mutated_norm:
            last_idx = i
    if last_idx is None:
        return 4
    return (len(files) - 1) - last_idx


def _name_leak(test_id: str, enclosing_function_name: str | None) -> bool:
    if not enclosing_function_name:
        return False
    test_name = test_id.split("::")[-1]
    test_tokens = set(test_name.split("_")) - {"test", ""}
    fn_tokens = set(enclosing_function_name.split("_")) - {""}
    return bool(test_tokens & fn_tokens)


def score_candidate(
    mutated_file: str,
    site: MutationSite,
    representative_test: str,
    full_output: str,
    failing_tests: list[str],
    total_tests: int,
    test_to_files: dict[str, set[str]],
) -> ScoreBreakdown:
    frames, _ = _extract_traceback_frames(full_output, representative_test)
    displacement = _displacement(mutated_file, frames)

    search_space = len(test_to_files.get(representative_test, set()))

    noise = (len(failing_tests) / total_tests) if total_tests else 1.0
    leak = _name_leak(representative_test, site.enclosing_function_name)

    d = min(displacement, 4) / 4
    s = min(search_space, 20) / 20
    n = 1 - min(noise * 40, 1)
    raw = 1 + 9 * (0.45 * d + 0.25 * s + 0.30 * n) - 1.0 * leak
    score = _clamp(1, 10, raw)

    return ScoreBreakdown(
        displacement=displacement,
        search_space=search_space,
        noise=noise,
        name_leak=leak,
        d=d,
        s=s,
        n=n,
        score=score,
    )


def classify_candidate(
    repo_dir: Path,
    python: str,
    baseline: Baseline,
    site: MutationSite,
    mutated_source: str,
    test_to_files: dict[str, set[str]],
    timeout: int = TIMEOUT_S,
) -> ClassificationResult:
    covering_tests = baseline.tests_for_line(site.path, site.lineno)

    if not covering_tests:
        return ClassificationResult(
            site=site, outcome=Outcome.TEST_GAP, covering_tests=[], reason="no covering tests in baseline"
        )

    targeted = run_mutation(repo_dir, python, site, mutated_source, covering_tests, timeout=timeout)

    if targeted.timed_out:
        return ClassificationResult(
            site=site, outcome=Outcome.DROP_TIMEOUT, covering_tests=covering_tests, reason="targeted run timed out"
        )
    if targeted.collection_error:
        return ClassificationResult(
            site=site,
            outcome=Outcome.DROP_CATASTROPHIC,
            covering_tests=covering_tests,
            reason="collection/import error",
        )
    if targeted.num_failed_or_errored == 0:
        return ClassificationResult(
            site=site,
            outcome=Outcome.TEST_GAP,
            covering_tests=covering_tests,
            reason="covering tests did not catch the mutation",
        )

    # tentative CANDIDATE -- confirm with a full-suite run for accurate
    # failing/total counts and a clean traceback.
    full = run_mutation(repo_dir, python, site, mutated_source, test_ids=None, timeout=timeout)

    if full.timed_out:
        return ClassificationResult(
            site=site, outcome=Outcome.DROP_TIMEOUT, covering_tests=covering_tests, reason="full-suite run timed out"
        )
    if full.collection_error:
        return ClassificationResult(
            site=site,
            outcome=Outcome.DROP_CATASTROPHIC,
            covering_tests=covering_tests,
            reason="collection/import error on full run",
        )

    total_tests = baseline.total_tests
    failing_tests = sorted(full.failing_tests)

    if not failing_tests:
        # The targeted run (covering tests only) failed, but the authoritative
        # full-suite run shows nothing failing -- most likely a test-ordering
        # or isolation dependency, not a reliably-caught mutation. Treat like
        # a test gap rather than crash on an empty representative-test pick.
        return ClassificationResult(
            site=site,
            outcome=Outcome.TEST_GAP,
            covering_tests=covering_tests,
            total_tests=total_tests,
            reason="targeted run failed but full-suite run did not reproduce any failure",
        )

    fraction_failed = (len(failing_tests) / total_tests) if total_tests else 1.0
    if fraction_failed > TOO_LOUD_FRACTION:
        return ClassificationResult(
            site=site,
            outcome=Outcome.DROP_TOO_LOUD,
            covering_tests=covering_tests,
            failing_tests=failing_tests,
            total_tests=total_tests,
            reason=f"{len(failing_tests)}/{total_tests} tests failed (> {TOO_LOUD_FRACTION:.0%})",
        )

    covering_and_failing = sorted(t for t in covering_tests if t in failing_tests)
    # Fall back to any failing test if none of the originally-covering tests
    # show up in the full run's failure list (nodeid format drift, or the
    # mutation's effect surfaced through a different test on the full run).
    representative_test = covering_and_failing[0] if covering_and_failing else failing_tests[0]
    breakdown = score_candidate(
        site.path,
        site,
        representative_test,
        full.stdout + "\n" + full.stderr,
        failing_tests,
        total_tests,
        test_to_files,
    )
    _, traceback_text = _extract_traceback_frames(full.stdout + "\n" + full.stderr, representative_test)

    outcome = Outcome.ADMITTED if breakdown.score >= ADMIT_THRESHOLD else Outcome.DROP_LOW_SCORE
    reason = "" if outcome == Outcome.ADMITTED else f"score {breakdown.score:.2f} < {ADMIT_THRESHOLD}"

    return ClassificationResult(
        site=site,
        outcome=outcome,
        covering_tests=covering_tests,
        failing_tests=failing_tests,
        total_tests=total_tests,
        traceback=traceback_text,
        representative_test=representative_test,
        score_breakdown=breakdown,
        reason=reason,
    )


def run_selection(
    repo_dir: Path,
    python: str,
    baseline: Baseline,
    sites_and_sources: list[tuple[MutationSite, str]],
    timeout: int = TIMEOUT_S,
) -> tuple[list[ClassificationResult], Counter]:
    """Classifies every candidate. Returns (results, rejection_taxonomy)."""
    test_to_files = _build_test_to_files(baseline)
    results = []
    taxonomy: Counter = Counter()
    for site, mutated_source in sites_and_sources:
        result = classify_candidate(repo_dir, python, baseline, site, mutated_source, test_to_files, timeout)
        results.append(result)
        taxonomy[result.outcome] += 1
    return results, taxonomy
