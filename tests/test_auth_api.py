"""Where sign-in meets the API: gating, identity, and the one-solve rule."""
from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from cloud import auth, progress
from cloud.handlers import fn_api, fn_grade

KEY = "api-test-signing-key"
USER = {"id": 4242, "login": "octocat", "avatar_url": "https://avatars/o.png"}
CHALLENGE = {"challenge_id": "tenacity-abc123", "repo": "jd__tenacity", "difficulty_score": 6.5}


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BUCKET", "b")
    monkeypatch.setenv("REPO_NAME", "jd__tenacity")
    monkeypatch.setenv("REPO_URL", "https://github.com/jd/tenacity")
    monkeypatch.setenv("TABLE_CHALLENGES", "challenges")
    monkeypatch.setenv("TABLE_GAPS", "gaps")
    monkeypatch.setenv("TABLE_SUBMISSIONS", "submissions")
    monkeypatch.setenv("TABLE_LEADERBOARD", "leaderboard")
    monkeypatch.setenv("TABLE_PROGRESS", "progress")
    monkeypatch.setenv("SESSION_SIGNING_SECRET_ARN_VALUE", KEY)
    monkeypatch.setenv("GRADE_FUNCTION_ARN", "arn:grade")
    fn_api._immutable.clear()


def _signed_in(**extra) -> dict:
    session = auth.make_session(USER, KEY.encode())
    return {"cookies": [f"{auth.COOKIE_NAME}={session}"], **extra}


@pytest.fixture
def grader(monkeypatch):
    """Captures the payload fn_api sends to fn_grade."""
    sent = {}

    class _Lambda:
        def invoke(self, **kwargs):
            sent.update(json.loads(kwargs["Payload"]))
            return {}

    monkeypatch.setattr(fn_api, "lambda_client", lambda: _Lambda())
    monkeypatch.setattr(fn_api.ddb_io, "get", lambda table, key: dict(CHALLENGE))
    monkeypatch.setattr(fn_api.ddb_io, "put", lambda table, item: None)
    return sent


# ---------------------------------------------------------------------------
# gating
# ---------------------------------------------------------------------------

def test_submitting_signed_out_is_refused(env, grader):
    response = fn_api.post_submission(
        {"body": json.dumps({"challenge_id": CHALLENGE["challenge_id"], "patch": "p"})}
    )
    assert response["statusCode"] == 401
    assert json.loads(response["body"])["error"] == "sign_in_required"
    assert grader == {}, "nothing may be graded without a session"


def test_submitting_with_a_forged_cookie_is_refused(env, grader):
    forged = auth.make_session(USER, b"not-the-real-key")
    response = fn_api.post_submission(
        {
            "cookies": [f"{auth.COOKIE_NAME}={forged}"],
            "body": json.dumps({"challenge_id": CHALLENGE["challenge_id"], "patch": "p"}),
        }
    )
    assert response["statusCode"] == 401
    assert grader == {}


def test_browsing_never_requires_a_session(env, monkeypatch):
    monkeypatch.setattr(fn_api.ddb_io, "get", lambda table, key: dict(CHALLENGE))
    monkeypatch.setattr(fn_api, "_scan", lambda table: [])
    monkeypatch.setattr(fn_api, "_query_repo", lambda table, repo: [dict(CHALLENGE)])
    for route, call in [
        ("GET /challenges/{id}", lambda: fn_api.get_challenge(CHALLENGE["challenge_id"])),
        ("GET /gaps", lambda: fn_api.get_gaps({})),
        ("GET /leaderboard", lambda: fn_api.get_leaderboard({})),
    ]:
        assert call()["statusCode"] == 200, route


def test_a_stack_with_no_signing_secret_stays_usable_signed_out(env, monkeypatch, grader):
    """Deploying without the auth secrets must not 500 every request -- the
    site still works, you simply cannot submit."""
    monkeypatch.delenv("SESSION_SIGNING_SECRET_ARN_VALUE")
    assert fn_api.get_me({})["statusCode"] == 200
    assert json.loads(fn_api.get_me({})["body"])["user"] is None
    assert fn_api.post_submission({"body": "{}"})["statusCode"] == 401


# ---------------------------------------------------------------------------
# identity
# ---------------------------------------------------------------------------

def test_the_user_id_comes_from_the_session_not_the_body(env, grader):
    """The whole point of the gate: a body-supplied user_id would let anyone
    write points into anyone else's leaderboard row."""
    response = fn_api.post_submission(
        _signed_in(
            body=json.dumps(
                {
                    "challenge_id": CHALLENGE["challenge_id"],
                    "patch": "p",
                    "user_id": "gh:1",  # someone else
                }
            )
        )
    )

    assert response["statusCode"] == 202
    assert grader["user_id"] == "gh:4242"
    assert grader["login"] == "octocat"


def test_me_reports_the_signed_in_profile(env):
    body = json.loads(fn_api.get_me(_signed_in())["body"])
    assert body["user"] == {
        "user_id": "gh:4242",
        "login": "octocat",
        "avatar_url": "https://avatars/o.png",
    }


def test_me_signed_out_is_a_200_with_no_user(env):
    response = fn_api.get_me({})
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"user": None}


def test_progress_signed_out_is_empty_rather_than_refused(env):
    body = json.loads(fn_api.get_progress({}, {})["body"])
    assert body == {"solved": [], "count": 0, "signed_in": False}


def test_progress_returns_the_signed_in_users_solves(env, monkeypatch):
    rows = [
        {"challenge_id": "c1", "repo": "jd__tenacity", "solved_at": 20},
        {"challenge_id": "c2", "repo": "jd__tenacity", "solved_at": 10},
    ]
    seen = {}
    monkeypatch.setattr(
        fn_api.progress, "solved", lambda uid, repo=None: seen.update(uid=uid, repo=repo) or rows
    )

    body = json.loads(fn_api.get_progress(_signed_in(), {"repo": "jd__tenacity"})["body"])

    assert seen == {"uid": "gh:4242", "repo": "jd__tenacity"}
    assert body["solved_ids"] == ["c1", "c2"]
    assert body["signed_in"] is True


def test_the_leaderboard_ranks_by_score_and_marks_you(env, monkeypatch):
    monkeypatch.setattr(
        fn_api,
        "_scan",
        lambda table: [
            {"user_id": "gh:7", "login": "alice", "solved": 2, "score": 9.0},
            {"user_id": "gh:4242", "login": "octocat", "solved": 5, "score": 21.5},
        ],
    )
    rows = json.loads(fn_api.get_leaderboard(_signed_in())["body"])["leaderboard"]

    assert [r["login"] for r in rows] == ["octocat", "alice"]
    assert [r["rank"] for r in rows] == [1, 2]
    assert [r["is_you"] for r in rows] == [True, False]


def test_the_leaderboard_marks_nobody_when_signed_out(env, monkeypatch):
    monkeypatch.setattr(
        fn_api, "_scan", lambda table: [{"user_id": "gh:7", "login": "alice", "solved": 1, "score": 3.0}]
    )
    rows = json.loads(fn_api.get_leaderboard({})["body"])["leaderboard"]
    assert rows[0]["is_you"] is False


# ---------------------------------------------------------------------------
# scoring a solve exactly once
# ---------------------------------------------------------------------------

def _conditional_failure():
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "exists"}}, "PutItem"
    )


class _Table:
    """A DynamoDB table stub that honours attribute_not_exists(challenge_id)."""

    def __init__(self):
        self.items = {}
        self.updates = []

    def put_item(self, Item, ConditionExpression=None):
        key = (Item["user_id"], Item["challenge_id"])
        if ConditionExpression and key in self.items:
            raise _conditional_failure()
        self.items[key] = Item

    def update_item(self, **kwargs):
        self.updates.append(kwargs)


@pytest.fixture
def tables(env, monkeypatch):
    table = _Table()
    monkeypatch.setattr(progress.ddb_io, "table", lambda name: table)
    monkeypatch.setattr(fn_grade.ddb_io, "table", lambda name: table)
    return table


def test_a_first_solve_is_recorded_and_scored(tables):
    fn_grade._award(
        {"user_id": "gh:4242", "login": "octocat", "avatar_url": "https://avatars/o.png"},
        dict(CHALLENGE),
        CHALLENGE["challenge_id"],
        seconds=271,
    )

    assert tables.items[("gh:4242", CHALLENGE["challenge_id"])]["seconds"] == 271
    assert len(tables.updates) == 1
    values = tables.updates[0]["ExpressionAttributeValues"]
    assert float(values[":points"]) == 6.5
    assert values[":login"] == "octocat"


def test_resolving_the_same_challenge_scores_nothing_extra(tables):
    user = {"user_id": "gh:4242", "login": "octocat", "avatar_url": ""}
    for _ in range(4):
        fn_grade._award(user, dict(CHALLENGE), CHALLENGE["challenge_id"], seconds=10)

    assert len(tables.updates) == 1, "a solved challenge must never be scored twice"


def test_a_second_challenge_is_scored_again(tables):
    user = {"user_id": "gh:4242", "login": "octocat", "avatar_url": ""}
    fn_grade._award(user, dict(CHALLENGE), "c1", seconds=None)
    fn_grade._award(user, dict(CHALLENGE), "c2", seconds=None)
    assert len(tables.updates) == 2


def test_an_anonymous_solve_never_reaches_the_leaderboard(tables):
    fn_grade._award({"user_id": "anonymous"}, dict(CHALLENGE), "c1", seconds=None)
    fn_grade._award({"user_id": ""}, dict(CHALLENGE), "c1", seconds=None)
    assert tables.updates == []
    assert tables.items == {}


def test_record_solve_omits_seconds_when_not_reported(tables):
    progress.record_solve("gh:1", "c1", "repo", 4.0, seconds=None)
    assert "seconds" not in tables.items[("gh:1", "c1")]


def test_record_solve_reports_first_versus_repeat(tables):
    assert progress.record_solve("gh:1", "c1", "repo", 4.0) is True
    assert progress.record_solve("gh:1", "c1", "repo", 4.0) is False
