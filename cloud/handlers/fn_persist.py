"""State 6: package the trees, write the tables, publish the gap report.

The answer-bearing fields -- the patch, the mutated file, the line, the
operator -- are deliberately NOT written to the challenges table. Grading
doesn't need them (it applies the learner's patch and runs the suite), so the
only copy outside the pipeline is the object under answers/, which no browser
is ever handed a URL for.

The gap report is rebuilt by listing the raw batch results in S3 rather than
from the Map's output, because a batch whose mutations were ALL test gaps
fails its branch by design and its output never reaches this state.
"""
from __future__ import annotations

import logging
import time
from pathlib import PurePosixPath

from bugforge.models import MutationSite
from bugforge.mutate import apply
from bugforge.package import package_challenge
from bugforge.select import ClassificationResult, Outcome, ScoreBreakdown

from cloud import config, ddb_io, s3_io, workspace

log = logging.getLogger()
log.setLevel(logging.INFO)


def _classification(record: dict, site: MutationSite) -> ClassificationResult:
    return ClassificationResult(
        site=site,
        outcome=Outcome.ADMITTED,
        covering_tests=record.get("covering_tests", []),
        failing_tests=record.get("failing_tests", []),
        total_tests=record.get("total_tests", 0),
        traceback=record.get("traceback", ""),
        representative_test=record.get("representative_test"),
        score_breakdown=ScoreBreakdown(**record["score_breakdown"]),
    )


def _persist_one(tree, bucket: str, commit_sha: str, record: dict, now: int) -> dict:
    site = MutationSite(**record["site"])
    challenge_id = record["challenge_id"]

    original = (tree / site.path).read_text(encoding="utf-8")
    mutated = apply(original, site)
    output_dir = workspace.scratch("package")

    challenge = package_challenge(
        repo_dir=tree,
        repo_name=config.repo_name(),
        commit_sha=commit_sha,
        site=site,
        original_source=original,
        mutated_source=mutated,
        classification=_classification(record, site),
        output_dir=output_dir,
        practice_mode=True,
    )

    tarball = next(output_dir.glob("*-public.tar.gz"))
    tree_key = s3_io.put_file(
        bucket, config.public_tree_key(challenge_id), tarball, "application/gzip"
    )
    traceback_key = s3_io.put_text(
        bucket, config.public_traceback_key(challenge_id), record.get("traceback", "")
    )
    # Separate prefix, separate IAM, never presigned. reveal.json carries the
    # site for the post-PASS result screen; only fn_reveal can read it.
    s3_io.put_text(bucket, config.answer_patch_key(challenge_id), challenge.diff)
    s3_io.put_json(
        bucket,
        config.answer_reveal_key(challenge_id),
        {
            **record["site"],
            "commit_sha": commit_sha,
            "original_line": original.splitlines()[site.lineno - 1],
            "mutated_line": mutated.splitlines()[site.lineno - 1],
        },
    )

    breakdown = record["score_breakdown"]
    item = {
        "challenge_id": challenge_id,
        "repo": config.repo_name(),
        "repo_url": config.repo_url(),
        "license": config.repo_license(),
        "language": config.repo_language_label(),
        "commit_sha": commit_sha,
        "title": record.get("title", ""),
        "description": record.get("description", ""),
        "difficulty_score": record["difficulty_score"],
        # The three inputs to the score, so no card ever shows one opaque
        # number. None of them locates the bug.
        "score_breakdown": {
            key: breakdown[key] for key in ("displacement", "search_space", "noise", "d", "s", "n")
        },
        "failing_tests": record["failing_tests"],
        "total_tests": record["total_tests"],
        "tree_key": tree_key,
        "traceback_key": traceback_key,
        "created_at": now,
    }
    ddb_io.put(config.table("challenges"), item)
    return item


def _persist_gaps(bucket: str, execution_id: str, commit_sha: str, now: int) -> list[dict]:
    gaps: list[dict] = []
    for key in s3_io.list_keys(bucket, config.raw_result_prefix(execution_id)):
        payload = s3_io.get_json(bucket, key)
        for record in payload["results"]:
            if record["outcome"] != Outcome.TEST_GAP:
                continue
            site = record["site"]
            gap_id = (
                f"{config.repo_name()}-{commit_sha[:10]}-"
                f"{PurePosixPath(site['path']).stem}-L{site['lineno']}-{site['operator_id']}"
            )
            gap = {
                "gap_id": gap_id,
                "repo": config.repo_name(),
                "commit_sha": commit_sha,
                "file_path": site["path"],
                "lineno": site["lineno"],
                "operator": site["operator_id"],
                "original_token": site["original_token"],
                "mutated_token": site["mutated_token"],
                "enclosing_function": site.get("enclosing_function_name"),
                "covering_test_count": len(record.get("covering_tests", [])),
                "reason": record.get("reason", ""),
                "created_at": now,
            }
            ddb_io.put(config.table("gaps"), gap)
            gaps.append(gap)
    return gaps


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    bucket = config.bucket()
    payload = s3_io.get_json(bucket, event["described_key"])
    commit_sha = payload["commit_sha"]
    now = int(time.time())

    tree = workspace.repo_tree(config.repo_dir())
    admitted = [r for r in payload["scored"] if r["outcome"] == Outcome.ADMITTED]

    written = []
    for record in admitted:
        # Each challenge is written to DynamoDB as it is packaged, so a
        # timeout here loses the tail of the run, never the whole run.
        written.append(_persist_one(tree, bucket, commit_sha, record, now))

    gaps = _persist_gaps(bucket, execution_id, commit_sha, now)

    taxonomy: dict[str, int] = {}
    for record in payload["scored"]:
        taxonomy[record["outcome"]] = taxonomy.get(record["outcome"], 0) + 1

    log.info("persisted %s challenges and %s test gaps", len(written), len(gaps))
    return {
        "execution_id": execution_id,
        "repo": config.repo_name(),
        "commit_sha": commit_sha,
        "challenges_written": len(written),
        "gaps_written": len(gaps),
        "challenge_ids": [item["challenge_id"] for item in written],
        "taxonomy": taxonomy,
    }
