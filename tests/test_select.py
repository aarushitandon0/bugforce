"""
Unit tests for bugforge.select's pure scoring/classification logic. These
are the parts most likely to silently miscompute a score or misclassify a
candidate without ever raising an exception -- so they're tested directly
against hand-built inputs rather than only through the slow end-to-end path.
"""
from __future__ import annotations

from pathlib import Path

import bugforge.select as select_module
from bugforge.models import Baseline, MutationSite
from bugforge.runner import RunResult
from bugforge.select import (
    Outcome,
    _displacement,
    _extract_traceback_frames,
    _name_leak,
    classify_candidate,
    score_candidate,
)

SAMPLE_OUTPUT = """
=================================== FAILURES ===================================
_____________________________ test_add_things ______________________________

    def test_add_things():
>       assert add(2, 3) == 5
E       assert 4 == 5

tests/test_ops.py:5: in test_add_things
    assert add(2, 3) == 5
mathy/ops.py:2: in add
    return a + b
=========================== short test summary info ============================
FAILED tests/test_ops.py::test_add_things - assert 4 == 5
1 failed, 1 passed in 0.12s
"""


def _site(operator_id="ARITHMETIC", fn="add"):
    return MutationSite(
        path="mathy/ops.py",
        lineno=2,
        col_start=11,
        col_end=12,
        operator_id=operator_id,
        original_token="+",
        mutated_token="-",
        enclosing_function_name=fn,
    )


def test_extract_traceback_frames_order_and_scoping():
    frames, block = _extract_traceback_frames(SAMPLE_OUTPUT, "tests/test_ops.py::test_add_things")
    assert frames == [
        ("tests/test_ops.py", 5, "test_add_things"),
        ("mathy/ops.py", 2, "add"),
    ]
    assert "assert 4 == 5" in block


def test_extract_traceback_frames_picks_the_right_test_block():
    two_test_output = SAMPLE_OUTPUT.replace(
        "=========================== short test summary info ============================",
        (
            "_____________________________ test_sub_things ______________________________\n"
            "\n"
            "tests/test_ops.py:9: in test_sub_things\n"
            "    assert sub(5, 3) == 1\n"
            "mathy/ops.py:5: in sub\n"
            "    return a - b\n"
            "=========================== short test summary info ============================"
        ),
    )
    frames, _ = _extract_traceback_frames(two_test_output, "tests/test_ops.py::test_sub_things")
    assert frames == [("tests/test_ops.py", 9, "test_sub_things"), ("mathy/ops.py", 5, "sub")]


REAL_LONG_OUTPUT = """
=================================== FAILURES ===================================
_______________ TestContextManager.test_retry_with_async_exc _______________

    async def test_retry_with_async_exc(self) -> None:
>       result = await test()

tests\\test_asyncio.py:450:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def __anext__(self) -> AttemptManager:
>           do = await self.iter(retry_state=self._retry_state)

tenacity\\asyncio\\__init__.py:204:
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _

    async def test() -> int:
>                   raise CustomException
E                   CustomException

tests\\test_asyncio.py:436: CustomException
=========================== short test summary info ============================
"""


def test_extract_traceback_frames_real_pytest_long_format():
    # pytest --tb=long prints "path:line: " (or "path:line: ExcName" for the
    # deepest frame) with no "in func", and backslashes on Windows. Phase 3's
    # original regex required "in func" and matched none of these, which made
    # every challenge's displacement 4.
    frames, _ = _extract_traceback_frames(
        REAL_LONG_OUTPUT, "tests/test_asyncio.py::TestContextManager::test_retry_with_async_exc"
    )
    assert frames == [
        ("tests\\test_asyncio.py", 450, ""),
        ("tenacity\\asyncio\\__init__.py", 204, ""),
        ("tests\\test_asyncio.py", 436, "CustomException"),
    ]
    assert _displacement("tenacity/asyncio/__init__.py", frames) == 1


def test_displacement_zero_when_mutated_file_is_deepest_frame():
    frames = [("tests/test_ops.py", 5, "test_add_things"), ("mathy/ops.py", 2, "add")]
    assert _displacement("mathy/ops.py", frames) == 0


def test_displacement_k_frames_above_deepest():
    frames = [
        ("tests/test_ops.py", 5, "test_add_things"),
        ("mathy/ops.py", 2, "add"),
        ("mathy/util.py", 10, "helper"),
        ("mathy/other.py", 20, "unrelated"),
    ]
    # mutated file is 2 frames above the deepest (index 1 of 3)
    assert _displacement("mathy/ops.py", frames) == 2


def test_displacement_best_case_when_file_absent_from_traceback():
    frames = [("tests/test_ops.py", 5, "test_add_things"), ("mathy/other.py", 2, "unrelated")]
    assert _displacement("mathy/ops.py", frames) == 4


def test_displacement_empty_traceback_is_best_case():
    assert _displacement("mathy/ops.py", []) == 4


def test_name_leak_true_when_tokens_overlap():
    assert _name_leak("tests/test_ops.py::test_add_things", "add") is True
    assert _name_leak("tests/test_ops.py::test_add_things", "add_things_up") is True


def test_name_leak_false_when_no_overlap():
    assert _name_leak("tests/test_ops.py::test_sub_things", "add") is False


def test_name_leak_false_when_no_enclosing_function():
    assert _name_leak("tests/test_ops.py::test_add_things", None) is False


def test_score_rewards_high_displacement_and_search_space():
    site = _site()
    test_to_files = {"tests/test_ops.py::test_add_things": {"mathy/ops.py", "mathy/util.py", "mathy/other.py"}}
    far_output = SAMPLE_OUTPUT.replace("mathy/ops.py:2: in add", "mathy/far_away.py:99: in far_away")
    breakdown = score_candidate(
        "mathy/ops.py",
        site,
        "tests/test_ops.py::test_add_things",
        far_output,
        ["tests/test_ops.py::test_add_things"],
        200,
        test_to_files,
    )
    assert breakdown.displacement == 4  # mutated file not in traceback at all
    assert breakdown.search_space == 3
    assert breakdown.score > 5.0


def test_score_penalizes_name_leak():
    site = _site(fn="add_things")  # shares "add" + "things" token with the test name
    test_to_files = {"tests/test_ops.py::test_add_things": {"mathy/ops.py"}}
    with_leak = score_candidate(
        "mathy/ops.py",
        site,
        "tests/test_ops.py::test_add_things",
        SAMPLE_OUTPUT,
        ["tests/test_ops.py::test_add_things"],
        200,
        test_to_files,
    )
    no_leak_site = _site(fn="compute")
    without_leak = score_candidate(
        "mathy/ops.py",
        no_leak_site,
        "tests/test_ops.py::test_add_things",
        SAMPLE_OUTPUT,
        ["tests/test_ops.py::test_add_things"],
        200,
        test_to_files,
    )
    assert with_leak.name_leak is True
    assert without_leak.name_leak is False
    assert with_leak.score == without_leak.score - 1.0


def test_score_is_clamped_to_1_and_10():
    site = _site()
    test_to_files: dict[str, set[str]] = {}
    # worst case: displacement 0, search_space 0, noise saturates n to 0, plus a leak
    worst_site = _site(fn="add_things")
    worst = score_candidate(
        "mathy/ops.py",
        worst_site,
        "tests/test_ops.py::test_add_things",
        SAMPLE_OUTPUT,
        ["tests/test_ops.py::test_add_things"] * 50,
        50,
        test_to_files,
    )
    assert 1.0 <= worst.score <= 10.0


def test_classify_candidate_falls_back_when_full_run_ids_dont_match_covering_tests(monkeypatch):
    # Reproduces a real crash: the targeted run (using baseline-derived
    # covering test ids) shows a failure, but the full-suite run's own
    # FAILED-line parsing yields test ids that don't literally match any of
    # the covering ids (nodeid format drift). representative_test selection
    # must fall back instead of indexing into an empty list.
    site = _site()
    baseline = Baseline(
        repo="acme__mathy",
        commit_sha="deadbeef",
        total_tests=10,
        green=True,
        line_to_tests={"mathy/ops.py:2": ["tests/test_ops.py::test_add"]},
    )

    targeted_result = RunResult(
        returncode=1, stdout="", stderr="", passed=0, failed=1, errors=0,
        timed_out=False, collection_error=False, failing_tests=["tests/test_ops.py::test_add"],
    )
    full_result = RunResult(
        returncode=1,
        stdout=SAMPLE_OUTPUT,
        stderr="",
        passed=9,
        failed=1,
        errors=0,
        timed_out=False,
        collection_error=False,
        # deliberately a DIFFERENT id than the covering test, to force the fallback path
        failing_tests=["tests/test_ops.py::test_add_things"],
    )
    calls = iter([targeted_result, full_result])
    monkeypatch.setattr(select_module, "run_mutation", lambda *a, **k: next(calls))

    result = classify_candidate(
        Path("."), "python", baseline, site, "mutated source", test_to_files={}
    )

    assert result.outcome in (Outcome.ADMITTED, Outcome.DROP_LOW_SCORE)
    assert result.representative_test == "tests/test_ops.py::test_add_things"


def test_classify_candidate_treats_unreproduced_failure_as_test_gap(monkeypatch):
    site = _site()
    baseline = Baseline(
        repo="acme__mathy",
        commit_sha="deadbeef",
        total_tests=10,
        green=True,
        line_to_tests={"mathy/ops.py:2": ["tests/test_ops.py::test_add"]},
    )
    targeted_result = RunResult(
        returncode=1, stdout="", stderr="", passed=0, failed=1, errors=0,
        timed_out=False, collection_error=False, failing_tests=["tests/test_ops.py::test_add"],
    )
    full_result = RunResult(
        returncode=0, stdout="", stderr="", passed=10, failed=0, errors=0,
        timed_out=False, collection_error=False, failing_tests=[],
    )
    calls = iter([targeted_result, full_result])
    monkeypatch.setattr(select_module, "run_mutation", lambda *a, **k: next(calls))

    result = classify_candidate(
        Path("."), "python", baseline, site, "mutated source", test_to_files={}
    )

    assert result.outcome == Outcome.TEST_GAP
