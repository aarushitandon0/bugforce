"""Load an already-forged corpus into the local stack, without the pipeline.

    python infra/local/seed.py phase5_output

The forge itself cannot run here. Every pipeline lambda is a container-image
function -- the repo and its test suite are baked into the image -- and
LocalStack **community** refuses to start one:

    Could not start new environment: NotImplementedError:
    Container images are a Pro feature.

Step Functions therefore fails on its first state, three retries deep, and the
tables stay empty, which is what an empty /repos and an empty /gaps are. The
two HTTP functions are unaffected, because `sam local start-api` runs them on
this machine's Docker rather than inside LocalStack.

So this writes what fn_persist would have written, from a real run's output
directory rather than from a live pipeline: the same DynamoDB items, the same
two S3 prefixes, the same keys. It substitutes for the *runtime*, not for the
pipeline -- the challenges, scores, tracebacks and trees here were all
produced by the real thing, offline.

Re-running is safe: every write is a put by primary key, so a second run
replaces the same items rather than adding to them.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

HTTP_FUNCTION = "ApiFunction"
NUL, CRLF, LF = bytes([0]), bytes([13, 10]), bytes([10])


def stack_environment(stack: str) -> dict:
    """The deployed ApiFunction's environment -- the same source env_vars.py uses.

    Reading it off the stack rather than restating it means this cannot drift
    from the stack it is seeding: the table names and the bucket are whatever
    CloudFormation actually created.
    """
    cfn = boto3.client("cloudformation")
    pages = cfn.get_paginator("list_stack_resources").paginate(StackName=stack)
    physical = {
        r["LogicalResourceId"]: r["PhysicalResourceId"]
        for page in pages
        for r in page["StackResourceSummaries"]
        if r["ResourceType"] == "AWS::Lambda::Function"
    }
    name = physical.get(HTTP_FUNCTION)
    if name is None:
        raise SystemExit(stack + " has no " + HTTP_FUNCTION + " -- deploy it first")
    config = boto3.client("lambda").get_function_configuration(FunctionName=name)
    return config.get("Environment", {}).get("Variables", {})


def site_key(site: dict) -> tuple:
    return (site["path"], site["lineno"], site["operator_id"])


def diff_lines(diff: str) -> tuple:
    """(original, mutated) from a one-line unified diff, without the +/- marker."""
    original = ""
    mutated = ""
    for line in diff.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-") and not original:
            original = line[1:]
        elif line.startswith("+") and not mutated:
            mutated = line[1:]
    return original, mutated


def lf_tarball(source: Path) -> Path:
    """A copy of a public tree with LF line endings in every text file.

    These trees were packaged on Windows, so the working files carry CRLF. The
    editor builds its patch from LF text, and `git apply` matches context
    byte for byte, so against a CRLF tree every submission -- including the
    correct one -- is rejected as `patch_did_not_apply`. The repo's own suite
    does not care which ending its sources use. `.git` is left alone: those
    are binary objects, not text.
    """
    fd, name = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)  # Windows will not let us delete a file with an open handle
    target = Path(name)
    with tarfile.open(source) as src, tarfile.open(target, "w:gz") as dst:
        for member in src.getmembers():
            data = None
            if member.isfile():
                data = src.extractfile(member).read()
                if NUL not in data and CRLF in data and "/.git/" not in "/" + member.name:
                    data = data.replace(CRLF, LF)
                member.size = len(data)
                dst.addfile(member, io.BytesIO(data))
            else:
                dst.addfile(member)
    return target


def read_member(tarball: Path, name: str) -> bytes:
    with tarfile.open(tarball) as tar:
        member = tar.extractfile(name)
        if member is None:
            raise SystemExit(str(tarball) + " has no " + name)
        return member.read()


def main(output_dir: str) -> int:
    out = Path(output_dir)
    if not out.is_dir():
        raise SystemExit(str(out) + " is not a directory")

    stack = os.environ.get("STACK_NAME", "bugforge-local")
    os.environ.update(stack_environment(stack))

    from cloud import config, ddb_io  # imported after the environment is in place

    selection = json.loads((out / "selection.json").read_text(encoding="utf-8"))
    challenges = json.loads((out / "challenges.json").read_text(encoding="utf-8"))
    commit_sha = selection["commit_sha"]
    results = selection["results"]

    # The run's classification records carry the traceback, the covering tests
    # and the score inputs; challenges.json carries the challenge_id and the
    # learner-facing title. They join on the mutation site.
    by_site = {site_key(r["site"]): r for r in results}

    bucket = config.bucket()
    s3 = boto3.client("s3")
    now = int(time.time())

    written = 0
    for entry in challenges:
        challenge_id = entry["challenge_id"]
        key = (entry["file_path"], entry["lineno"], entry["operator"])
        record = by_site.get(key)
        if record is None:
            print("  skip " + challenge_id + ": no run record for " + str(key), file=sys.stderr)
            continue

        directory = out / challenge_id
        public = next(directory.glob("*-public.tar.gz"), None)
        answers = next(directory.glob("*-answers.tar.gz"), None)
        detail_path = next(directory.glob("*.json"), None)
        if public is None or answers is None or detail_path is None:
            print("  skip " + challenge_id + ": " + str(directory) + " is incomplete", file=sys.stderr)
            continue
        detail = json.loads(detail_path.read_text(encoding="utf-8"))

        # public/: handed to the browser as a presigned URL.
        normalized = lf_tarball(public)
        s3.upload_file(
            str(normalized),
            bucket,
            config.public_tree_key(challenge_id),
            ExtraArgs={"ContentType": "application/gzip"},
        )
        normalized.unlink()
        s3.put_object(
            Bucket=bucket,
            Key=config.public_traceback_key(challenge_id),
            Body=record.get("traceback", "").encode("utf-8"),
            ContentType="text/plain",
        )

        # answers/: separate prefix, separate IAM, never presigned.
        site = record["site"]
        original_line, mutated_line = diff_lines(detail["diff"])
        s3.put_object(
            Bucket=bucket,
            Key=config.answer_patch_key(challenge_id),
            Body=read_member(answers, "mutation.patch").replace(CRLF, LF),
            ContentType="text/plain",
        )
        reveal = dict(site)
        reveal["commit_sha"] = commit_sha
        reveal["original_line"] = original_line
        reveal["mutated_line"] = mutated_line
        s3.put_object(
            Bucket=bucket,
            Key=config.answer_reveal_key(challenge_id),
            Body=json.dumps(reveal).encode("utf-8"),
            ContentType="application/json",
        )

        breakdown = record["score_breakdown"]
        ddb_io.put(
            config.table("challenges"),
            {
                "challenge_id": challenge_id,
                "repo": config.repo_name(),
                "repo_url": config.repo_url(),
                "license": config.repo_license(),
                "language": config.repo_language_label(),
                "commit_sha": commit_sha,
                "title": entry.get("title", ""),
                "description": entry.get("description", ""),
                "difficulty_score": entry["difficulty_score"],
                "score_breakdown": {
                    k: breakdown[k]
                    for k in ("displacement", "search_space", "noise", "d", "s", "n")
                },
                "failing_tests": record["failing_tests"],
                "total_tests": record["total_tests"],
                "tree_key": config.public_tree_key(challenge_id),
                "traceback_key": config.public_traceback_key(challenge_id),
                "created_at": now,
            },
        )
        written += 1

    gaps = 0
    for record in results:
        if record["outcome"] != "TEST_GAP":
            continue
        site = record["site"]
        gap_id = "{}-{}-{}-L{}-{}".format(
            config.repo_name(),
            commit_sha[:10],
            Path(site["path"]).stem,
            site["lineno"],
            site["operator_id"],
        )
        ddb_io.put(
            config.table("gaps"),
            {
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
            },
        )
        gaps += 1

    print("seeded {} challenges and {} test gaps into {}".format(written, gaps, stack))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "phase5_output"))
