"""The post-PASS reveal: the mutation diff, the exact token, and the real file.

This is the only function whose role can read answers/{id}/, and it returns
the answer only for a submission that has already PASSED against the repo's
own suite. It returns content, never a presigned URL, and it is invoked by
fn_api rather than exposed directly -- so the API role still cannot read
answers/ at all.
"""
from __future__ import annotations

import logging

from cloud import config, ddb_io, s3_io

log = logging.getLogger()
log.setLevel(logging.INFO)

PASS = "PASS"  # fn_grade.PASS


def github_blob_url(repo_url: str, commit_sha: str, path: str, lineno: int) -> str:
    base = repo_url.strip().rstrip("/").removesuffix(".git")
    return f"{base}/blob/{commit_sha}/{path}#L{lineno}"


def handler(event: dict, context) -> dict:
    submission_id = event.get("submission_id") or ""
    submission = ddb_io.get(config.table("submissions"), {"submission_id": submission_id})
    if not submission:
        return {"status": 404, "body": {"error": "no such submission"}}
    if submission.get("verdict") != PASS:
        return {
            "status": 403,
            "body": {"error": "the mutation is revealed only after a passing submission"},
        }

    challenge_id = submission["challenge_id"]
    challenge = ddb_io.get(config.table("challenges"), {"challenge_id": challenge_id}) or {}
    bucket = config.bucket()
    site = s3_io.get_json(bucket, config.answer_reveal_key(challenge_id))
    diff = s3_io.get_text(bucket, config.answer_patch_key(challenge_id))

    repo_url = challenge.get("repo_url") or config.repo_url()
    commit_sha = site.get("commit_sha") or challenge.get("commit_sha", "")
    log.info("revealing %s for %s", challenge_id, submission_id)

    return {
        "status": 200,
        "body": {
            "submission_id": submission_id,
            "challenge_id": challenge_id,
            "tests_passed": submission.get("tests_passed", 0),
            "title": challenge.get("title", ""),
            "repo": challenge.get("repo", config.repo_name()),
            "repo_url": repo_url,
            "license": challenge.get("license", ""),
            "commit_sha": commit_sha,
            "file_path": site["path"],
            "lineno": site["lineno"],
            "col_start": site["col_start"],
            "col_end": site["col_end"],
            "operator": site["operator_id"],
            "original_token": site["original_token"],
            "mutated_token": site["mutated_token"],
            "original_line": site["original_line"],
            "mutated_line": site["mutated_line"],
            "diff": diff,
            "github_url": github_blob_url(repo_url, commit_sha, site["path"], site["lineno"]),
        },
    }
