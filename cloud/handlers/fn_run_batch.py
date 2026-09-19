"""State 3 (inside the Map): apply and run 15 mutations, keep the survivors.

This is the targeted half of Phase 3's classifier: for a mutation on line L it
runs ONLY the tests the baseline says execute line L. A mutation none of those
tests catch is a TEST_GAP -- useful to the repo's maintainers, useless as a
challenge -- and is recorded, not promoted. Whatever survives goes on to
fn_score for the authoritative full-suite pass.

Two things about the control flow here matter:

1. The raw per-mutation results are written to S3 BEFORE this function decides
   whether to fail. fn_persist recovers gap and drop data by listing that
   prefix, so a "failed" batch still contributes its test gaps.
2. A batch with no survivors raises. That is the normal case, not an error --
   see the ToleratedFailurePercentage comment in the state machine.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from bugforge.models import Baseline, MutationSite
from bugforge.mutate import MutationError
from bugforge.runner import run_mutation_with
from bugforge.select import TIMEOUT_S, Outcome

from cloud import config, s3_io, workspace

log = logging.getLogger()
log.setLevel(logging.INFO)

SURVIVOR = "SURVIVOR"


class NoSurvivorsError(RuntimeError):
    """Every mutation in the batch was a gap or a drop. Expected; see the ASL."""


def _classify_one(tree, adapter, runner, baseline: Baseline, site: MutationSite) -> dict:
    covering = baseline.tests_for_line(site.path, site.lineno)
    record = {"site": asdict(site), "covering_tests": covering}

    if not covering:
        return {**record, "outcome": Outcome.TEST_GAP, "reason": "no covering tests in baseline"}

    source = (tree / site.path).read_text(encoding="utf-8")
    try:
        mutated = adapter.apply(source, site)
    except MutationError as e:
        return {**record, "outcome": Outcome.DROP_CATASTROPHIC, "reason": f"apply failed: {e}"}

    result = run_mutation_with(adapter, tree, site, mutated, covering, runner)

    if result.timed_out:
        return {**record, "outcome": Outcome.DROP_TIMEOUT, "reason": "targeted run timed out"}
    if result.collection_error:
        return {**record, "outcome": Outcome.DROP_CATASTROPHIC, "reason": "collection/import error"}
    if result.num_failed_or_errored == 0:
        return {
            **record,
            "outcome": Outcome.TEST_GAP,
            "reason": "covering tests did not catch the mutation",
        }
    return {**record, "outcome": SURVIVOR, "reason": "", "targeted_failures": result.failing_tests}


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    batch_id = event["batch_id"]
    batch = s3_io.get_json(config.bucket(), event["batch_key"])
    baseline = Baseline(**s3_io.get_json(config.bucket(), event["baseline_key"]))

    tree = workspace.repo_tree(config.repo_dir())
    adapter = workspace.adapter()
    runner = workspace.runner_config(TIMEOUT_S)

    results = [
        _classify_one(tree, adapter, runner, baseline, MutationSite(**site))
        for site in batch["sites"]
    ]
    survivors = [r for r in results if r["outcome"] == SURVIVOR]

    # Durable first, fail second: fn_persist reads this prefix for the gap
    # report whether or not the branch below raises.
    raw_key = s3_io.put_json(
        config.bucket(),
        config.raw_result_key(execution_id, batch_id),
        {
            "execution_id": execution_id,
            "batch_id": batch_id,
            "commit_sha": baseline.commit_sha,
            "total_tests": baseline.total_tests,
            "results": results,
        },
    )

    log.info("%s: %s/%s survived the targeted run", batch_id, len(survivors), len(results))

    if not survivors:
        raise NoSurvivorsError(f"{batch_id}: no mutation survived the targeted run")

    # Only a summary travels back through the state machine. The survivors
    # themselves stay in the raw file above, which fn_score reads directly --
    # a list of covering test ids per survivor would otherwise push the Map's
    # aggregate output past the 256KB state payload limit on a large repo.
    return {
        "execution_id": execution_id,
        "batch_id": batch_id,
        "raw_key": raw_key,
        "survivor_count": len(survivors),
        "gap_count": sum(1 for r in results if r["outcome"] == Outcome.TEST_GAP),
    }
