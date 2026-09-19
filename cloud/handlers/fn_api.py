"""The HTTP surface. One router; the interesting part is what it refuses to say.

Challenge responses carry the title, description, repo, licence, difficulty
and its three inputs, and the failing test names. They never carry the patch,
the mutated file, the line, or the operator: a learner who knows the file and
line has already solved it. Those fields are not in the challenges table to
begin with (see fn_persist), so this is belt and braces. The challenge id is
opaque for the same reason (see cloud/ids.py).

GET /challenges/{id}/tree presigns the PUBLIC prefix only, for ten minutes.
This function's execution role has no read access to answers/{id}/, so a bug
in the code below still cannot produce a URL to mutation.patch. The post-PASS
diff comes from fn_reveal, which has its own role and checks the verdict
itself; this function only relays its answer.

GET /forge/{id} is the live generation stream. It shows every classified
mutation, rejects included -- but a mutation that is (or may become) a
challenge is shown with its location masked, because the stream is public.

Identity comes from the session cookie and from nowhere else. POST
/submissions requires one; every other route stays open, because browsing
repos, challenges and the gap report needs no account. The user id is read
off the verified session, never off the request body -- a body-supplied id
would let anyone write to anyone else's leaderboard row.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid

import boto3
from boto3.dynamodb.conditions import Key

from bugforge.select import Outcome

from cloud import auth, config, ddb_io, ids, progress, s3_io

log = logging.getLogger()
log.setLevel(logging.INFO)

TREE_URL_TTL_S = 600

# fn_run_batch.SURVIVOR; not imported so the API does not load the runner.
SURVIVOR = "SURVIVOR"
def masked_location(path: str) -> str:
    """The stream's stand-in for a location it must not reveal.

    The extension is kept because it gives nothing away -- every file in a
    given repo shares it -- while a fixed ".py" would be a visible lie on a Go
    repo's stream and would make the masked rows stand out from the unmasked
    ones, which is the opposite of what masking is for.
    """
    suffix = path.rpartition(".")[2]
    return f"░░░░░░.{suffix}:░░░" if suffix and suffix != path else "░░░░░░:░░░"
# Admission starts at 3.0 and the score is clamped to 10: seven one-point bins.
HISTOGRAM_EDGES = [3, 4, 5, 6, 7, 8, 9, 10]

_sfn = None
_lambda = None

# S3 objects that never change once written (baseline, batches, finished batch
# results, the final scored file). A warm container polls the same execution
# every second or two, so it reads each of those once.
_immutable: dict[str, object] = {}


def sfn():
    global _sfn
    if _sfn is None:
        _sfn = boto3.client("stepfunctions")
    return _sfn


def lambda_client():
    global _lambda
    if _lambda is None:
        _lambda = boto3.client("lambda")
    return _lambda


def _response(status: int, payload) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": ddb_io.dumps(payload),
    }


def _session(event: dict) -> dict | None:
    """The verified signed-in user, or None. Never raises: a stack deployed
    without auth secrets stays fully usable signed-out."""
    try:
        return auth.read_session(event, auth.signing_key())
    except auth.AuthError:
        log.warning("auth is not configured; treating request as signed out")
        return None


def _require_session(event: dict) -> tuple[dict | None, dict | None]:
    """Returns (claims, error_response). Exactly one is not None."""
    claims = _session(event)
    if claims is None:
        return None, _response(
            401, {"error": "sign_in_required", "message": "Sign in with GitHub to submit a fix."}
        )
    return claims, None


def _body(event: dict) -> dict:
    raw = event.get("body") or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _get_immutable_json(bucket: str, key: str):
    if key not in _immutable:
        _immutable[key] = s3_io.get_json(bucket, key)
    return _immutable[key]


def _forgeable() -> list[dict]:
    return [{"repo": config.repo_name(), "url": config.repo_url()}]


# ---------------------------------------------------------------------------
# forge
# ---------------------------------------------------------------------------

def post_forge(event: dict) -> dict:
    body = _body(event)
    repo_url = (body.get("repo_url") or "").strip() or config.repo_url()

    # Refuse before starting an execution: the image only contains the vetted
    # repo, and fn_baseline would refuse anyway -- this just says so up front.
    if config.normalize_repo_url(repo_url) != config.normalize_repo_url(config.repo_url()):
        return _response(
            422,
            {
                "error": "not_vetted",
                "message": f"{repo_url} is not vetted yet. Repos are forged from images built "
                "ahead of time, with dependencies installed on a trusted machine.",
                "forgeable": _forgeable(),
            },
        )

    execution_id = f"forge-{uuid.uuid4().hex[:12]}"
    response = sfn().start_execution(
        stateMachineArn=os.environ["STATE_MACHINE_ARN"],
        name=execution_id,
        input=json.dumps({"execution_id": execution_id, "repo_url": config.repo_url()}),
    )
    return _response(
        202,
        {
            "execution_id": execution_id,
            "execution_arn": response["executionArn"],
            "repo_url": config.repo_url(),
        },
    )


def _stream_row(record: dict, scored: dict | None) -> dict:
    """One line of the generation stream, from a raw batch result and (for a
    survivor) its full-suite scoring record, if scoring has reached it."""
    site = record["site"]
    row = {
        "id": ids.site_digest(site["path"], site["lineno"], site["operator_id"], site["mutated_token"]),
        "operator": site["operator_id"],
    }
    location = f"{site['path']}:{site['lineno']}"
    outcome = record["outcome"]

    if outcome == SURVIVOR:
        if scored is None:
            return {
                **row,
                "verdict": "scoring",
                "location": masked_location(site["path"]),
                "tests_red": len(record.get("targeted_failures") or []),
                "detail": "full suite…",
            }
        record, outcome = scored, scored["outcome"]
        if outcome == Outcome.TEST_GAP:
            # The covering tests failed but the full suite did not reproduce
            # it. Not reliably caught, and not in the gap report either.
            return {**row, "verdict": "drop", "location": location, "tests_red": 0,
                    "detail": "not reproducible"}

    failing = len(record.get("failing_tests") or [])
    if outcome == Outcome.ADMITTED:
        return {**row, "verdict": "keep", "location": masked_location(site["path"]), "tests_red": failing,
                "detail": f"displacement {record['score_breakdown']['displacement']}"}
    if outcome == Outcome.TEST_GAP:
        return {**row, "verdict": "gap", "location": location, "tests_red": 0,
                "detail": "test gap → report"}
    details = {
        Outcome.DROP_TOO_LOUD: (failing, "too loud"),
        Outcome.DROP_LOW_SCORE: (failing, "too easy"),
        Outcome.DROP_TIMEOUT: (None, "timeout"),
        Outcome.DROP_CATASTROPHIC: (None, "import error"),
    }
    tests_red, detail = details.get(outcome, (None, outcome.lower()))
    return {**row, "verdict": "drop", "location": location, "tests_red": tests_red, "detail": detail}


def build_stream(raw_payloads: list[dict], scored_records: list[dict]) -> list[dict]:
    """Rows in classification order. Pure, so it is unit-tested directly."""
    scored_by_id = {
        ids.site_digest(r["site"]["path"], r["site"]["lineno"], r["site"]["operator_id"], r["site"]["mutated_token"]): r
        for r in scored_records
    }
    rows = []
    for payload in raw_payloads:
        for record in payload["results"]:
            site = record["site"]
            digest = ids.site_digest(site["path"], site["lineno"], site["operator_id"], site["mutated_token"])
            rows.append(_stream_row(record, scored_by_id.get(digest)))
    return rows


def _phase(status: str, has_baseline: bool, batch_count: int, raw_count: int, scoring_done: bool) -> str:
    if status == "SUCCEEDED":
        return "done"
    if status != "RUNNING":
        return "failed"
    if not has_baseline:
        return "baseline"
    if batch_count == 0:
        return "generate"
    if raw_count < batch_count:
        return "run"
    if not scoring_done:
        return "score"
    return "package"


def get_forge(execution_id: str) -> dict:
    arn = f"{os.environ['STATE_MACHINE_ARN'].replace(':stateMachine:', ':execution:')}:{execution_id}"
    try:
        execution = sfn().describe_execution(executionArn=arn)
    except sfn().exceptions.ExecutionDoesNotExist:
        return _response(404, {"error": "no such execution"})

    bucket = config.bucket()
    keys = set(s3_io.list_keys(bucket, config.work_prefix(execution_id)))

    baseline = None
    if config.baseline_key(execution_id) in keys:
        payload = _get_immutable_json(bucket, config.baseline_key(execution_id))
        baseline = {"total_tests": payload["total_tests"], "covered_lines": len(payload["line_to_tests"])}

    batch_keys = sorted(k for k in keys if k.startswith(config.batch_prefix(execution_id)))
    candidates = sum(len(_get_immutable_json(bucket, k)["sites"]) for k in batch_keys)

    raw_keys = sorted(k for k in keys if k.startswith(config.raw_result_prefix(execution_id)))
    raw_payloads = [_get_immutable_json(bucket, k) for k in raw_keys]

    scoring_done = config.scored_key(execution_id) in keys
    if scoring_done:
        scored_records = _get_immutable_json(bucket, config.scored_key(execution_id))["scored"]
    elif config.pending_key(execution_id) in keys:
        scored_records = s3_io.get_json(bucket, config.pending_key(execution_id))["scored"]
    else:
        scored_records = []

    rows = build_stream(raw_payloads, scored_records)
    counts = {verdict: sum(1 for r in rows if r["verdict"] == verdict)
              for verdict in ("keep", "drop", "gap", "scoring")}

    status = execution["status"]
    summary = None
    if status == "SUCCEEDED" and execution.get("output"):
        output = json.loads(execution["output"])
        summary = {
            "challenges_ready": output.get("challenges_written", 0),
            "test_gaps": output.get("gaps_written", 0),
            "repo": output.get("repo"),
        }

    return _response(
        200,
        {
            "execution_id": execution_id,
            "repo_url": json.loads(execution.get("input") or "{}").get("repo_url"),
            "status": status,
            "phase": _phase(status, baseline is not None, len(batch_keys), len(raw_keys), scoring_done),
            "started_at": execution["startDate"].isoformat(),
            "stopped_at": execution["stopDate"].isoformat() if execution.get("stopDate") else None,
            "error": execution.get("error"),
            "cause": execution.get("cause"),
            "baseline": baseline,
            "candidates": candidates,
            "batches": len(batch_keys),
            "batches_done": len(raw_keys),
            "rows": rows,
            "counts": counts,
            "summary": summary,
        },
    )


# ---------------------------------------------------------------------------
# browse
# ---------------------------------------------------------------------------

def _scan(table_name: str) -> list[dict]:
    table = ddb_io.table(table_name)
    items, kwargs = [], {}
    while True:
        page = table.scan(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return ddb_io.from_ddb(items)


def _query_repo(table_name: str, repo: str) -> list[dict]:
    table = ddb_io.table(table_name)
    items, kwargs = [], {"IndexName": "repo_index", "KeyConditionExpression": Key("repo").eq(repo)}
    while True:
        page = table.query(**kwargs)
        items.extend(page.get("Items", []))
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return ddb_io.from_ddb(items)


def difficulty_label(score: float) -> str:
    if score < 5:
        return "easy"
    if score < 7:
        return "medium"
    return "hard"


def histogram(scores: list[float]) -> list[int]:
    counts = [0] * (len(HISTOGRAM_EDGES) - 1)
    for score in scores:
        index = int(score) - HISTOGRAM_EDGES[0]
        counts[max(0, min(index, len(counts) - 1))] += 1
    return counts


def challenge_card(item: dict) -> dict:
    """The learner-facing projection of a challenges row. Allow-list, not deny-list."""
    breakdown = item.get("score_breakdown")
    score = float(item.get("difficulty_score", 0))
    return {
        "challenge_id": item["challenge_id"],
        "repo": item.get("repo", ""),
        "repo_url": item.get("repo_url", ""),
        "license": item.get("license", ""),
        "language": item.get("language", "Python"),
        "title": item.get("title", ""),
        "description": item.get("description", ""),
        "difficulty_score": score,
        "difficulty_label": difficulty_label(score),
        "breakdown": (
            {key: breakdown[key] for key in ("displacement", "search_space", "noise", "d", "s", "n")}
            if breakdown
            else None
        ),
        "failing_test_count": len(item.get("failing_tests", [])),
        "total_tests": item.get("total_tests", 0),
    }


def get_repos() -> dict:
    challenges = _scan(config.table("challenges"))
    gaps = _scan(config.table("gaps"))

    by_repo: dict[str, list[dict]] = {}
    for item in challenges:
        by_repo.setdefault(item["repo"], []).append(item)
    gap_counts: dict[str, int] = {}
    for gap in gaps:
        gap_counts[gap["repo"]] = gap_counts.get(gap["repo"], 0) + 1

    repos = []
    for repo in sorted(set(by_repo) | set(gap_counts)):
        items = by_repo.get(repo, [])
        scores = [float(i.get("difficulty_score", 0)) for i in items]
        first = items[0] if items else {}
        repos.append(
            {
                "repo": repo,
                "repo_url": first.get("repo_url", ""),
                "license": first.get("license", ""),
                "language": first.get("language", "Python"),
                "challenge_count": len(items),
                "avg_difficulty": round(sum(scores) / len(scores), 2) if scores else 0,
                "histogram": histogram(scores),
                "gap_count": gap_counts.get(repo, 0),
            }
        )

    return _response(
        200, {"repos": repos, "histogram_edges": HISTOGRAM_EDGES, "forgeable": _forgeable()}
    )


def get_challenges(params: dict) -> dict:
    repo = params.get("repo")
    items = _query_repo(config.table("challenges"), repo) if repo else _scan(config.table("challenges"))
    listing = sorted(
        (challenge_card(item) for item in items),
        key=lambda c: (c["difficulty_score"], c["challenge_id"]),
    )
    return _response(200, {"challenges": listing, "count": len(listing)})


def get_challenge(challenge_id: str) -> dict:
    item = ddb_io.get(config.table("challenges"), {"challenge_id": challenge_id})
    if not item:
        return _response(404, {"error": "no such challenge"})
    return _response(
        200,
        {
            **challenge_card(item),
            "failing_tests": item.get("failing_tests", []),
            "tree_url_path": f"/challenges/{challenge_id}/tree",
        },
    )


def get_tree(challenge_id: str) -> dict:
    item = ddb_io.get(config.table("challenges"), {"challenge_id": challenge_id})
    if not item:
        return _response(404, {"error": "no such challenge"})

    key = item["tree_key"]
    if not key.startswith(f"{config.PUBLIC_PREFIX}/"):
        # Unreachable via the write path, but presigning is the one operation
        # where a mistake is unrecoverable, so it is checked here too.
        log.error("refusing to presign non-public key %s", key)
        return _response(500, {"error": "challenge bundle is not in the public prefix"})

    return _response(
        200,
        {
            "challenge_id": challenge_id,
            "url": s3_io.presign(config.bucket(), key, TREE_URL_TTL_S),
            "traceback_url": s3_io.presign(
                config.bucket(), item["traceback_key"], TREE_URL_TTL_S
            ),
            "expires_in": TREE_URL_TTL_S,
        },
    )


def get_gaps(params: dict) -> dict:
    repo = params.get("repo")
    items = _query_repo(config.table("gaps"), repo) if repo else _scan(config.table("gaps"))
    items.sort(key=lambda g: (g.get("file_path", ""), g.get("lineno", 0), g.get("operator", "")))
    return _response(200, {"gaps": items, "count": len(items)})


# ---------------------------------------------------------------------------
# submissions
# ---------------------------------------------------------------------------

MAX_VISITS = 300
MAX_VISIT_PATH = 200


def sanitize_investigation(raw) -> list[dict]:
    """The learner's file-open log: [{path, at}], at = epoch ms.

    Untrusted display data -- it is replayed on the result screen and never
    influences grading -- so it is length-capped and stripped of anything that
    isn't a plausible path and timestamp.
    """
    if not isinstance(raw, list):
        return []
    visits = []
    for entry in raw[:MAX_VISITS]:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        at = entry.get("at")
        if not isinstance(path, str) or not path or len(path) > MAX_VISIT_PATH:
            continue
        if isinstance(at, bool) or not isinstance(at, (int, float)):
            continue
        visits.append({"path": path, "at": int(at)})
    return sorted(visits, key=lambda v: v["at"])


def post_submission(event: dict) -> dict:
    claims, denied = _require_session(event)
    if denied:
        return denied

    body = _body(event)
    challenge_id = body.get("challenge_id")
    patch = body.get("patch")
    if not challenge_id or not patch:
        return _response(400, {"error": "challenge_id and patch are required"})
    challenge = ddb_io.get(config.table("challenges"), {"challenge_id": challenge_id})
    if not challenge:
        return _response(404, {"error": "no such challenge"})

    # From the verified session only. body["user_id"] is ignored if present.
    user_id = auth.user_id(claims)
    submission_id = f"sub-{uuid.uuid4().hex[:12]}"
    item = {
        "submission_id": submission_id,
        "challenge_id": challenge_id,
        "user_id": user_id,
        "login": claims.get("login", ""),
        "status": "PENDING",
        "created_at": int(time.time()),
    }
    # fn_grade merges its verdict into this item, so the log survives grading
    # and comes back from GET /submissions/{id} for the replay.
    investigation = sanitize_investigation(body.get("investigation"))
    if investigation:
        item["investigation"] = investigation
    ddb_io.put(config.table("submissions"), item)

    # Grading runs for as long as the suite takes; the client polls.
    lambda_client().invoke(
        FunctionName=os.environ["GRADE_FUNCTION_ARN"],
        InvocationType="Event",
        Payload=json.dumps(
            {
                "submission_id": submission_id,
                "challenge_id": challenge_id,
                "patch": patch,
                "user_id": user_id,
                "login": claims.get("login", ""),
                "avatar_url": claims.get("avatar", ""),
                "seconds": body.get("seconds"),
            }
        ).encode("utf-8"),
    )
    return _response(202, {"submission_id": submission_id, "status": "PENDING"})


def get_submission(submission_id: str) -> dict:
    item = ddb_io.get(config.table("submissions"), {"submission_id": submission_id})
    if not item:
        return _response(404, {"error": "no such submission"})
    return _response(200, item)


def get_reveal(submission_id: str) -> dict:
    """Relays fn_reveal. The PASS check lives there, next to the only role that
    can read the answer, not here."""
    response = lambda_client().invoke(
        FunctionName=os.environ["REVEAL_FUNCTION_ARN"],
        InvocationType="RequestResponse",
        Payload=json.dumps({"submission_id": submission_id}).encode("utf-8"),
    )
    if response.get("FunctionError"):
        raise RuntimeError(f"fn_reveal failed: {response['Payload'].read()[:500]!r}")
    result = json.loads(response["Payload"].read())
    return _response(result["status"], result["body"])


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------
# Reading a session needs the signing key and nothing else, so these live here
# rather than in fn_auth -- routing them through the one function that can talk
# to GitHub as the application would widen that role for no reason.

LEADERBOARD_LIMIT = 50


def get_me(event: dict) -> dict:
    """200 with the profile when signed in, 200 with user=None when not.

    Not 401: "are you signed in?" is a question every page asks on load, and
    an unauthenticated answer is a normal answer, not an error.
    """
    claims = _session(event)
    if not claims:
        return _response(200, {"user": None})
    return _response(200, {"user": auth.public_profile(claims)})


def get_progress(event: dict, params: dict) -> dict:
    """The signed-in user's solved challenges. Signed out, the client keeps
    using its local record, so this answers with an empty list rather than
    refusing."""
    claims = _session(event)
    if not claims:
        return _response(200, {"solved": [], "count": 0, "signed_in": False})
    solved = progress.solved(auth.user_id(claims), params.get("repo"))
    return _response(
        200,
        {
            "solved": solved,
            "solved_ids": [row["challenge_id"] for row in solved],
            "count": len(solved),
            "signed_in": True,
        },
    )


def get_leaderboard(event: dict) -> dict:
    rows = _scan(config.table("leaderboard"))
    rows.sort(key=lambda r: (-float(r.get("score", 0)), -int(r.get("solved", 0)), r.get("login", "")))
    top = [
        {
            "rank": i + 1,
            "user_id": row.get("user_id", ""),
            "login": row.get("login", "") or row.get("user_id", ""),
            "avatar_url": row.get("avatar_url", ""),
            "solved": int(row.get("solved", 0)),
            "score": round(float(row.get("score", 0)), 1),
        }
        for i, row in enumerate(rows[:LEADERBOARD_LIMIT])
    ]
    claims = _session(event)
    me = auth.user_id(claims) if claims else None
    for row in top:
        row["is_you"] = row["user_id"] == me
    return _response(200, {"leaderboard": top, "count": len(top)})


# ---------------------------------------------------------------------------
# router
# ---------------------------------------------------------------------------

def handler(event: dict, context) -> dict:
    route = event.get("routeKey", "")
    params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}

    try:
        if route == "POST /forge":
            return post_forge(event)
        if route == "GET /forge/{execution_id}":
            return get_forge(params["execution_id"])
        if route == "GET /repos":
            return get_repos()
        if route == "GET /challenges":
            return get_challenges(query)
        if route == "GET /challenges/{challenge_id}":
            return get_challenge(params["challenge_id"])
        if route == "GET /challenges/{challenge_id}/tree":
            return get_tree(params["challenge_id"])
        if route == "POST /submissions":
            return post_submission(event)
        if route == "GET /submissions/{submission_id}":
            return get_submission(params["submission_id"])
        if route == "GET /submissions/{submission_id}/reveal":
            return get_reveal(params["submission_id"])
        if route == "GET /gaps":
            return get_gaps(query)
        if route == "GET /auth/me":
            return get_me(event)
        if route == "GET /me/progress":
            return get_progress(event, query)
        if route == "GET /leaderboard":
            return get_leaderboard(event)
    except Exception:
        log.exception("unhandled error on %s", route)
        return _response(500, {"error": "internal error"})

    return _response(404, {"error": f"no route for {route}"})
