"""The run report: everything the pipeline computes and used to print to stdout.

The rejection taxonomy, the test-gap list, and the per-candidate score
components are the inputs to the writeup and the blog post. They were being
computed and then dropped on the floor, so this module turns a finished
selection run into one JSON file.

Deliberately dumb: a dict and time.perf_counter(), no framework.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bugforge.select import ClassificationResult, Outcome

# Every outcome is listed even when its count is zero, so a run that dropped
# nothing for a given reason still shows that reason on the slide.
ALL_OUTCOMES = [
    Outcome.ADMITTED,
    Outcome.TEST_GAP,
    Outcome.DROP_LOW_SCORE,
    Outcome.DROP_TOO_LOUD,
    Outcome.DROP_TIMEOUT,
    Outcome.DROP_CATASTROPHIC,
]


@dataclass
class Stopwatch:
    """Named wall-clock spans. `with sw.stage("baseline"): ...`"""

    stages: dict[str, float] = field(default_factory=dict)

    def stage(self, name: str) -> "_Span":
        return _Span(self, name)

    def record(self, name: str, seconds: float) -> None:
        self.stages[name] = round(seconds, 3)


class _Span:
    def __init__(self, sw: Stopwatch, name: str) -> None:
        self._sw = sw
        self._name = name

    def __enter__(self) -> "_Span":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self._sw.record(self._name, time.perf_counter() - self._t0)


def _score_components(result: ClassificationResult) -> dict | None:
    sb = result.score_breakdown
    if sb is None:
        return None
    return {
        "displacement": sb.displacement,
        "search_space": sb.search_space,
        "noise": round(sb.noise, 6),
        "name_leak": sb.name_leak,
        "d": round(sb.d, 4),
        "s": round(sb.s, 4),
        "n": round(sb.n, 4),
        "score": round(sb.score, 4),
    }


def _gap_row(result: ClassificationResult) -> dict:
    site = result.site
    return {
        "path": site.path,
        "lineno": site.lineno,
        "operator": site.operator_id,
        "mutation": f"{site.original_token} -> {site.mutated_token}",
        "original_token": site.original_token,
        "mutated_token": site.mutated_token,
        "enclosing_function": site.enclosing_function_name,
        "covering_tests": len(result.covering_tests),
        "reason": result.reason,
    }


def _admitted_row(result: ClassificationResult) -> dict:
    site = result.site
    return {
        "path": site.path,
        "lineno": site.lineno,
        "operator": site.operator_id,
        "mutation": f"{site.original_token} -> {site.mutated_token}",
        "enclosing_function": site.enclosing_function_name,
        "failing_test": result.representative_test,
        "failing_tests": len(result.failing_tests),
        "total_tests": result.total_tests,
        "score_breakdown": _score_components(result),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _median(values: list[float]) -> float | None:
    """Report this next to the mean, always.

    A laptop that suspends mid-run leaves perf_counter counting: one tenacity
    run came back with a 24,069s "targeted run" that dragged the mean from 2.4s
    to 203s and made the timing split meaningless. The median survives that;
    the mean is what tells you it happened.
    """
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 3)
    return round((ordered[mid - 1] + ordered[mid]) / 2, 3)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(len(ordered) * fraction), len(ordered) - 1)
    return round(ordered[index], 3)


def build_run_report(
    repo: str,
    commit_sha: str,
    results: list[ClassificationResult],
    candidates_generated: int,
    candidates_on_covered_lines: int,
    stopwatch: Stopwatch,
    baseline_cache_hit: bool,
    baseline_total_tests: int,
) -> dict:
    """Turns a finished selection run into the report dict."""
    taxonomy = {outcome: 0 for outcome in ALL_OUTCOMES}
    for r in results:
        taxonomy[r.outcome] = taxonomy.get(r.outcome, 0) + 1

    admitted = [r for r in results if r.outcome == Outcome.ADMITTED]
    gaps = [r for r in results if r.outcome == Outcome.TEST_GAP]

    targeted_times = [r.targeted_seconds for r in results if r.targeted_seconds > 0]
    full_times = [r.full_suite_seconds for r in results if r.full_suite_ran]
    displacements = [r.score_breakdown.displacement for r in admitted if r.score_breakdown]

    stages = dict(stopwatch.stages)
    stages["targeted_runs_total"] = round(sum(targeted_times), 3)
    stages["full_suite_runs_total"] = round(sum(full_times), 3)

    return {
        "repo": repo,
        "commit_sha": commit_sha,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline": {
            "cache_hit": baseline_cache_hit,
            "total_tests": baseline_total_tests,
        },
        "counts": {
            "candidates_generated": candidates_generated,
            "candidates_on_covered_lines": candidates_on_covered_lines,
            "candidates_classified": len(results),
            "admitted": len(admitted),
            "test_gaps": len(gaps),
        },
        "taxonomy": taxonomy,
        "timings_seconds": {
            **stages,
            "targeted_runs_count": len(targeted_times),
            "targeted_runs_mean": _mean(targeted_times),
            "targeted_runs_median": _median(targeted_times),
            "targeted_runs_p90": _percentile(targeted_times, 0.9),
            "targeted_runs_max": round(max(targeted_times), 3) if targeted_times else None,
            "full_suite_runs_count": len(full_times),
            "full_suite_runs_mean": _mean(full_times),
            "full_suite_runs_median": _median(full_times),
            "full_suite_runs_p90": _percentile(full_times, 0.9),
            "full_suite_runs_max": round(max(full_times), 3) if full_times else None,
        },
        "score_summary": {
            "mean_displacement": _mean([float(d) for d in displacements]),
            "mean_score": _mean([r.score_breakdown.score for r in admitted if r.score_breakdown]),
            "name_leak_count": sum(1 for r in admitted if r.score_breakdown and r.score_breakdown.name_leak),
        },
        "gaps": [_gap_row(r) for r in gaps],
        "admitted": [_admitted_row(r) for r in admitted],
    }


def write_run_report(report: dict, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "run_report.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return path


def format_taxonomy(report: dict) -> str:
    """The rejection table, as it should appear on the slide."""
    taxonomy = report["taxonomy"]
    total = sum(taxonomy.values())
    lines = []
    for outcome in ALL_OUTCOMES:
        count = taxonomy.get(outcome, 0)
        pct = (count / total * 100) if total else 0.0
        lines.append(f"  {outcome:<22} {count:>5}  ({pct:5.1f}%)")
    lines.append(f"  {'TOTAL':<22} {total:>5}")
    return "\n".join(lines)
