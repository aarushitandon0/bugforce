"""State 4: the authoritative full-suite pass over the survivors.

For each mutation that the targeted run caught, this runs the repo's ENTIRE
suite once. That gives three things the targeted run cannot: the true
failing/total ratio (so a mutation that breaks a quarter of the suite can be
dropped as too loud), a clean --tb=long traceback to ship to the learner, and
the inputs to the difficulty score.

Continuation: a full suite run costs seconds, and thirty survivors will not
fit in one 300s lambda. Rather than guess, this handler works against its own
remaining-time budget and reports `done: false` when it runs out; the state
machine loops straight back into it. Everything completed so far is already in
S3, so a continuation never repeats work.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from bugforge.models import Baseline, MutationSite
from bugforge.mutate import apply
from bugforge.runner import run_mutation
from bugforge.select import (
    ADMIT_THRESHOLD,
    TIMEOUT_S,
    TOO_LOUD_FRACTION,
    Outcome,
    _build_test_to_files,
    _extract_traceback_frames,
    score_candidate,
)

from cloud import config, ids, s3_io, workspace
from cloud.handlers.fn_run_batch import SURVIVOR

log = logging.getLogger()
log.setLevel(logging.INFO)

# One full-suite run plus a tree copy; bail out of the loop with at least this
# much of the lambda's clock left rather than risk a hard timeout mid-run.
RESERVE_MS = 90_000


def _pending_key(execution_id: str) -> str:
    return config.pending_key(execution_id)


def _score_one(tree, python, baseline: Baseline, test_to_files, survivor: dict) -> dict:
    site = MutationSite(**survivor["site"])
    source = (tree / site.path).read_text(encoding="utf-8")
    mutated = apply(source, site)

    full = run_mutation(tree, python, site, mutated, test_ids=None, timeout=TIMEOUT_S)
    record = {"site": survivor["site"], "covering_tests": survivor["covering_tests"]}

    if full.timed_out:
        return {**record, "outcome": Outcome.DROP_TIMEOUT, "reason": "full-suite run timed out"}
    if full.collection_error:
        return {
            **record,
            "outcome": Outcome.DROP_CATASTROPHIC,
            "reason": "collection/import error on full run",
        }

    failing = sorted(full.failing_tests)
    total = baseline.total_tests
    if not failing:
        # The covering tests failed but the full suite doesn't reproduce it --
        # a test-ordering dependency, not a reliably caught mutation.
        return {
            **record,
            "outcome": Outcome.TEST_GAP,
            "reason": "targeted run failed but full-suite run did not reproduce any failure",
        }

    fraction = (len(failing) / total) if total else 1.0
    if fraction > TOO_LOUD_FRACTION:
        return {
            **record,
            "outcome": Outcome.DROP_TOO_LOUD,
            "failing_tests": failing,
            "total_tests": total,
            "reason": f"{len(failing)}/{total} tests failed (> {TOO_LOUD_FRACTION:.0%})",
        }

    covering_and_failing = sorted(t for t in survivor["covering_tests"] if t in failing)
    representative = covering_and_failing[0] if covering_and_failing else failing[0]
    output = full.stdout + "\n" + full.stderr
    breakdown = score_candidate(
        site.path, site, representative, output, failing, total, test_to_files
    )
    _, traceback_text = _extract_traceback_frames(output, representative)

    admitted = breakdown.score >= ADMIT_THRESHOLD
    return {
        **record,
        "outcome": Outcome.ADMITTED if admitted else Outcome.DROP_LOW_SCORE,
        "challenge_id": ids.challenge_id(
            config.repo_name(),
            baseline.commit_sha,
            site.path,
            site.lineno,
            site.operator_id,
            site.mutated_token,
        ),
        "failing_tests": failing,
        "total_tests": total,
        "representative_test": representative,
        "traceback": traceback_text,
        "score_breakdown": asdict(breakdown),
        "difficulty_score": breakdown.score,
        "reason": "" if admitted else f"score {breakdown.score:.2f} < {ADMIT_THRESHOLD}",
    }


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    bucket = config.bucket()
    pending_key = _pending_key(execution_id)

    if event.get("continuation"):
        state = s3_io.get_json(bucket, pending_key)
        pending, scored = state["pending"], state["scored"]
    else:
        # Survivors are collected from the raw batch files rather than from the
        # Map's output: the output carries summaries only, and a batch whose
        # branch failed still wrote its file (it just has no survivors in it).
        pending = [
            record
            for key in s3_io.list_keys(bucket, config.raw_result_prefix(execution_id))
            for record in s3_io.get_json(bucket, key)["results"]
            if record["outcome"] == SURVIVOR
        ]
        scored = []

    baseline = Baseline(**s3_io.get_json(bucket, event["baseline_key"]))
    test_to_files = _build_test_to_files(baseline)
    tree = workspace.repo_tree(config.repo_dir())
    python = workspace.python_exe()

    while pending and context.get_remaining_time_in_millis() > RESERVE_MS:
        scored.append(_score_one(tree, python, baseline, test_to_files, pending.pop(0)))

    s3_io.put_json(bucket, pending_key, {"pending": pending, "scored": scored})
    admitted = [r for r in scored if r["outcome"] == Outcome.ADMITTED]
    done = not pending

    if done:
        s3_io.put_json(
            bucket,
            config.scored_key(execution_id),
            {"execution_id": execution_id, "commit_sha": baseline.commit_sha, "scored": scored},
        )

    log.info(
        "scored %s survivors (%s admitted), %s pending", len(scored), len(admitted), len(pending)
    )
    return {
        "execution_id": execution_id,
        "baseline_key": event["baseline_key"],
        "continuation": True,
        "done": done,
        "scored_key": config.scored_key(execution_id) if done else None,
        "scored_count": len(scored),
        "pending_count": len(pending),
        "admitted_count": len(admitted),
    }
