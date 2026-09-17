"""Grading. No model, no hidden tests, no heuristics.

    a. patch hygiene, via the AST (see cloud/anti_cheat.py)
    b. apply the patch to the broken tree
    c. run the FULL suite
    d. all green -> PASS, otherwise FAIL with the still-failing test names

The repo's own suite is the oracle. That works precisely because the mutation
was selected for being caught by that suite: a green run means the defect is
gone, and no separate answer key is consulted -- this lambda has no read
access to the answers prefix at all.

Invoked asynchronously; the submission row is updated in place and the client
polls GET /submissions/{id}.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from bugforge.runner import run_pytest

from cloud import anti_cheat, config, ddb_io, s3_io, workspace

log = logging.getLogger()
log.setLevel(logging.INFO)

PASS = "PASS"
FAIL = "FAIL"
REJECTED = "REJECTED"

# The lambda's own timeout is 60s; leave room to record the verdict.
SUITE_TIMEOUT_S = 40


class PatchApplyError(RuntimeError):
    pass


def _extract_tree(bucket: str, challenge_id: str) -> Path:
    scratch = workspace.scratch("grade")
    tarball = scratch / "tree.tar.gz"
    s3_io.get_file(bucket, config.public_tree_key(challenge_id), tarball)

    extracted = scratch / "extracted"
    with tarfile.open(tarball, "r:gz") as tar:
        tar.extractall(extracted, filter="data")
    tarball.unlink()

    roots = [p for p in extracted.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise RuntimeError(f"expected one top-level directory in the bundle, found {len(roots)}")
    return roots[0]


def _apply_patch(tree: Path, patch_text: str) -> None:
    patch_file = tree.parent / "submission.patch"
    # Normalized to \n and newline-terminated: git apply rejects a patch whose
    # final hunk line has no terminator, which is what a browser textarea
    # usually produces.
    normalized = patch_text.replace("\r\n", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    patch_file.write_text(normalized, encoding="utf-8")

    errors = []
    for strip in ("-p1", "-p0"):
        proc = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", strip, str(patch_file)],
            cwd=tree,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return
        errors.append(f"{strip}: {proc.stderr.strip()}")
    raise PatchApplyError("; ".join(errors))


def _record(submission_id: str, fields: dict) -> dict:
    item = ddb_io.get(config.table("submissions"), {"submission_id": submission_id}) or {
        "submission_id": submission_id
    }
    item.update(fields)
    item["completed_at"] = int(time.time())
    item["status"] = "COMPLETE"
    ddb_io.put(config.table("submissions"), item)
    return item


def _award(user_id: str, challenge_id: str, difficulty: float) -> None:
    ddb_io.table(config.table("leaderboard")).update_item(
        Key={"user_id": user_id},
        UpdateExpression="ADD solved :one, score :points SET updated_at = :now",
        ExpressionAttributeValues=ddb_io.to_ddb(
            {":one": 1, ":points": difficulty, ":now": int(time.time())}
        ),
    )


def handler(event: dict, context) -> dict:
    submission_id = event["submission_id"]
    challenge_id = event["challenge_id"]
    patch_text = event["patch"]
    user_id = event.get("user_id") or "anonymous"
    bucket = config.bucket()

    # (a) hygiene -- path rules first, so a patch aimed at a test file never
    # touches the tree at all.
    paths = anti_cheat.patch_target_paths(patch_text)
    path_check = anti_cheat.check_paths(paths)
    if not path_check.ok:
        return _record(
            submission_id,
            {"verdict": REJECTED, "reason": path_check.reason, "detail": path_check.detail},
        )

    workspace.configure()
    original = _extract_tree(bucket, challenge_id)
    work = original.parent / "work"
    shutil.copytree(original, work)

    # (b) apply
    try:
        _apply_patch(work, patch_text)
    except (PatchApplyError, subprocess.TimeoutExpired) as e:
        return _record(
            submission_id,
            {"verdict": REJECTED, "reason": "patch_did_not_apply", "detail": str(e)[:2000]},
        )

    # (a, continued) the content rules need the applied result to compare against
    diff_check = anti_cheat.check_tree_diff(original, work, path_check.touched_paths)
    if not diff_check.ok:
        return _record(
            submission_id,
            {"verdict": REJECTED, "reason": diff_check.reason, "detail": diff_check.detail},
        )

    # (c) full suite
    result = run_pytest(work, workspace.python_exe(), test_ids=None, timeout=SUITE_TIMEOUT_S)

    if result.timed_out:
        return _record(
            submission_id,
            {"verdict": FAIL, "reason": "suite timed out", "failing_tests": []},
        )
    if result.collection_error:
        return _record(
            submission_id,
            {
                "verdict": FAIL,
                "reason": "the patched tree does not import",
                "failing_tests": sorted(result.failing_tests),
            },
        )

    # (d) verdict
    green = result.num_failed_or_errored == 0 and result.returncode == 0
    if green:
        challenge = ddb_io.get(config.table("challenges"), {"challenge_id": challenge_id}) or {}
        _award(user_id, challenge_id, float(challenge.get("difficulty_score", 0)))
        return _record(
            submission_id,
            {
                "verdict": PASS,
                "reason": "",
                "failing_tests": [],
                "tests_passed": result.passed,
                "user_id": user_id,
            },
        )

    return _record(
        submission_id,
        {
            "verdict": FAIL,
            "reason": f"{result.num_failed_or_errored} test(s) still failing",
            "failing_tests": sorted(result.failing_tests),
            "tests_passed": result.passed,
            "user_id": user_id,
        },
    )
