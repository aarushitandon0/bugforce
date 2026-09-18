"""The run report is the only surviving record of what the pipeline rejected,
so the counts, the timing split, and the score components it writes are worth
testing directly."""
from __future__ import annotations

import json

from bugforge.models import MutationSite
from bugforge.run_report import (
    ALL_OUTCOMES,
    Stopwatch,
    build_run_report,
    format_taxonomy,
    write_run_report,
)
from bugforge.select import ClassificationResult, Outcome, ScoreBreakdown


def _site(path="pkg/retry.py", lineno=10, fn="retry_if_result", cls=None) -> MutationSite:
    return MutationSite(
        path=path,
        lineno=lineno,
        col_start=0,
        col_end=2,
        operator_id="RETURN",
        original_token="x",
        mutated_token="None",
        enclosing_function_name=fn,
        enclosing_class_name=cls,
    )


def _breakdown(displacement=2, score=7.0) -> ScoreBreakdown:
    return ScoreBreakdown(
        displacement=displacement,
        search_space=8,
        noise=0.01,
        name_leak=False,
        d=displacement / 4,
        s=0.4,
        n=0.6,
        score=score,
    )


def _admitted(displacement=2, score=7.0, targeted=1.0, full=9.0) -> ClassificationResult:
    return ClassificationResult(
        site=_site(),
        outcome=Outcome.ADMITTED,
        covering_tests=["t1"],
        failing_tests=["t1"],
        total_tests=100,
        representative_test="t1",
        score_breakdown=_breakdown(displacement, score),
        targeted_seconds=targeted,
        full_suite_seconds=full,
        full_suite_ran=True,
    )


def _gap(lineno=42) -> ClassificationResult:
    return ClassificationResult(
        site=_site(lineno=lineno, fn="wait_fixed", cls="wait_base"),
        outcome=Outcome.TEST_GAP,
        covering_tests=["t1", "t2"],
        reason="covering tests did not catch the mutation",
        targeted_seconds=0.5,
    )


def _report(results, **overrides):
    kwargs = dict(
        repo="jd__tenacity",
        commit_sha="abc123",
        results=results,
        candidates_generated=500,
        candidates_on_covered_lines=200,
        stopwatch=Stopwatch(),
        baseline_cache_hit=True,
        baseline_total_tests=2847,
    )
    kwargs.update(overrides)
    return build_run_report(**kwargs)


def test_taxonomy_lists_every_outcome_even_at_zero():
    """A reason that dropped nothing still belongs on the rejection slide."""
    report = _report([_admitted(), _gap()])
    assert set(report["taxonomy"]) == set(ALL_OUTCOMES)
    assert report["taxonomy"][Outcome.ADMITTED] == 1
    assert report["taxonomy"][Outcome.TEST_GAP] == 1
    assert report["taxonomy"][Outcome.DROP_TOO_LOUD] == 0


def test_counts_separate_generated_from_covered_from_admitted():
    report = _report([_admitted(), _admitted(), _gap()])
    assert report["counts"] == {
        "candidates_generated": 500,
        "candidates_on_covered_lines": 200,
        "candidates_classified": 3,
        "admitted": 2,
        "test_gaps": 1,
    }


def test_timing_splits_targeted_runs_from_full_suite_runs():
    """The whole point of the split: only survivors pay for a full-suite run."""
    results = [
        _admitted(targeted=1.0, full=9.0),
        _admitted(targeted=3.0, full=11.0),
        _gap(),  # targeted 0.5, no full run
    ]
    timings = _report(results)["timings_seconds"]
    assert timings["targeted_runs_total"] == 4.5
    assert timings["targeted_runs_count"] == 3
    assert timings["targeted_runs_mean"] == 1.5
    assert timings["full_suite_runs_total"] == 20.0
    assert timings["full_suite_runs_count"] == 2
    assert timings["full_suite_runs_mean"] == 10.0


def test_median_survives_a_run_that_the_machine_slept_through():
    """One suspended run took 24,069s and made the mean useless.

    The median has to stay honest, and the mean has to stay visible so the
    contamination is detectable rather than silent.
    """
    results = [_admitted(targeted=2.0, full=6.0) for _ in range(4)]
    results.append(_admitted(targeted=24069.0, full=13349.0))
    timings = _report(results)["timings_seconds"]
    assert timings["targeted_runs_median"] == 2.0
    assert timings["full_suite_runs_median"] == 6.0
    assert timings["targeted_runs_max"] == 24069.0
    assert timings["targeted_runs_mean"] > 4000  # the tell


def test_median_of_an_even_number_of_runs_averages_the_middle_pair():
    results = [_admitted(targeted=t) for t in (1.0, 2.0, 4.0, 8.0)]
    assert _report(results)["timings_seconds"]["targeted_runs_median"] == 3.0


def test_percentile_and_max_are_none_when_nothing_ran():
    timings = _report([])["timings_seconds"]
    assert timings["targeted_runs_median"] is None
    assert timings["targeted_runs_p90"] is None
    assert timings["targeted_runs_max"] is None


def test_stopwatch_stages_are_carried_into_the_report():
    watch = Stopwatch()
    watch.record("baseline", 12.3456)
    watch.record("generate_candidates", 1.2)
    timings = _report([_admitted()], stopwatch=watch)["timings_seconds"]
    assert timings["baseline"] == 12.346  # rounded to ms
    assert timings["generate_candidates"] == 1.2


def test_mean_displacement_comes_from_admitted_only():
    """Gaps have no score breakdown; they must not drag the mean toward zero."""
    report = _report([_admitted(displacement=1), _admitted(displacement=4), _gap()])
    assert report["score_summary"]["mean_displacement"] == 2.5


def test_score_summary_is_none_when_nothing_was_admitted():
    report = _report([_gap()])
    assert report["score_summary"]["mean_displacement"] is None
    assert report["score_summary"]["mean_score"] is None


def test_gap_rows_carry_what_a_maintainer_needs():
    report = _report([_gap(lineno=77)])
    (gap,) = report["gaps"]
    assert gap["path"] == "pkg/retry.py"
    assert gap["lineno"] == 77
    assert gap["operator"] == "RETURN"
    assert gap["mutation"] == "x -> None"
    assert gap["enclosing_function"] == "wait_fixed"
    assert gap["covering_tests"] == 2


def test_admitted_rows_carry_the_score_components_not_just_the_score():
    report = _report([_admitted(displacement=3, score=8.25)])
    (row,) = report["admitted"]
    breakdown = row["score_breakdown"]
    assert breakdown["displacement"] == 3
    assert breakdown["search_space"] == 8
    assert breakdown["name_leak"] is False
    assert breakdown["score"] == 8.25


def test_baseline_cache_hit_is_recorded():
    """Whether the slow step actually ran changes how the timing reads."""
    assert _report([_admitted()], baseline_cache_hit=True)["baseline"]["cache_hit"] is True
    assert _report([_admitted()], baseline_cache_hit=False)["baseline"]["cache_hit"] is False


def test_write_run_report_round_trips_as_json(tmp_path):
    report = _report([_admitted(), _gap()])
    path = write_run_report(report, tmp_path)
    assert path.name == "run_report.json"
    assert json.loads(path.read_text(encoding="utf-8")) == report


def test_format_taxonomy_renders_every_outcome_with_a_total():
    text = format_taxonomy(_report([_admitted(), _gap()]))
    for outcome in ALL_OUTCOMES:
        assert outcome in text
    assert "TOTAL" in text
