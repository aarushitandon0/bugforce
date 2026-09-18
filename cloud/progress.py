"""
Per-user solved state, so progress follows you between devices.

Before sign-in, "solved" lived only in the browser's localStorage. That is
still where an anonymous learner's progress lives; this table is what a signed-
in one gets instead.

One row per (user, challenge) solved, written by fn_grade on PASS and read by
fn_api for the course screen. The write is conditional on the row not already
existing, and **that condition is what makes the leaderboard correct**: the
award only happens when the conditional put succeeds, so re-solving a
challenge you have already solved adds nothing. Without it, re-submitting the
same fix repeatedly is an unbounded score.
"""
from __future__ import annotations

import time

from botocore.exceptions import ClientError

from cloud import config, ddb_io


def record_solve(
    user_id: str, challenge_id: str, repo: str, difficulty: float, seconds: int | None = None
) -> bool:
    """Marks a challenge solved. Returns True only on the FIRST solve.

    The caller uses the return value to decide whether to award points, so
    "already solved" must be distinguishable from "just solved" and neither
    may raise.
    """
    item = {
        "user_id": user_id,
        "challenge_id": challenge_id,
        "repo": repo,
        "difficulty_score": difficulty,
        "solved_at": int(time.time()),
    }
    if seconds is not None:
        item["seconds"] = int(seconds)
    try:
        ddb_io.table(config.table("progress")).put_item(
            Item=ddb_io.to_ddb(item),
            ConditionExpression="attribute_not_exists(challenge_id)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise
    return True


def solved(user_id: str, repo: str | None = None) -> list[dict]:
    """Every challenge this user has solved, newest first."""
    from boto3.dynamodb.conditions import Key

    response = ddb_io.table(config.table("progress")).query(
        KeyConditionExpression=Key("user_id").eq(user_id)
    )
    items = ddb_io.from_ddb(response.get("Items", []))
    if repo:
        items = [i for i in items if i.get("repo") == repo]
    items.sort(key=lambda i: i.get("solved_at", 0), reverse=True)
    return items
