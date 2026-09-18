"""Unit tests for fn_api and fn_reveal.

What these defend:
  * the public generation stream never shows where a kept (or still-scoring)
    mutation lives, while rejects and test gaps are shown in full;
  * no challenge response carries an answer-bearing field;
  * a repo that isn't vetted is refused before any execution starts;
  * the diff is revealed only for a PASS submission.
"""
from __future__ import annotations

import io
import json
from datetime import datetime, timezone

import pytest

from bugforge.select import Outcome
from cloud import auth, ids
from cloud.handlers import fn_api, fn_reveal


def _site(path="tenacity/retry.py", lineno=45, op="RETURN", token="None"):
    return {
        "path": path,
        "lineno": lineno,
        "col_start": 15,
        "col_end": 47,
        "operator_id": op,
        "original_token": "retry_all(*other.retries, self)",
        "mutated_token": token,
        "enclosing_function_name": "__rand__",
    }


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BUCKET", "b")
    monkeypatch.setenv("REPO_NAME", "jd__tenacity")
    monkeypatch.setenv("REPO_URL", "https://github.com/jd/tenacity")
    monkeypatch.setenv("REPO_LICENSE", "Apache-2.0")
    monkeypatch.setenv("TABLE_CHALLENGES", "challenges")
    monkeypatch.setenv("TABLE_GAPS", "gaps")
    monkeypatch.setenv("TABLE_SUBMISSIONS", "submissions")
    monkeypatch.setenv(
        "STATE_MACHINE_ARN", "arn:aws:states:us-east-1:111:stateMachine:bugforge-forge"
    )
    fn_api._immutable.clear()


def _body(response):
    return json.loads(response["body"])


SIGNING_KEY = "test-signing-key"


@pytest.fixture
def signed_in(monkeypatch):
    """A request event carrying a valid session cookie for one GitHub user."""
    monkeypatch.setenv("SESSION_SIGNING_SECRET_ARN_VALUE", SIGNING_KEY)

    def event(**extra):
        session = auth.make_session(
            {"id": 4242, "login": "octocat", "avatar_url": "https://avatars/octocat.png"},
            SIGNING_KEY.encode(),
        )
        return {"cookies": [f"{auth.COOKIE_NAME}={session}"], **extra}

    return event


# --------------------------------------------------------------------------
# generation stream
# --------------------------------------------------------------------------

def test_stream_masks_kept_and_scoring_rows_but_shows_rejects():
    gap = {"site": _site("tenacity/wait.py", 88), "outcome": Outcome.TEST_GAP}
    timeout = {"site": _site("tenacity/stop.py", 10), "outcome": Outcome.DROP_TIMEOUT}
    kept = {"site": _site("tenacity/retry.py", 45), "outcome": "SURVIVOR", "targeted_failures": ["t1"]}
    loud = {"site": _site("tenacity/__init__.py", 412), "outcome": "SURVIVOR", "targeted_failures": ["t"]}
    pending = {"site": _site("tenacity/before.py", 42), "outcome": "SURVIVOR", "targeted_failures": ["a", "b"]}
    scored = [
        {"site": kept["site"], "outcome": Outcome.ADMITTED, "failing_tests": ["t1"],
         "score_breakdown": {"displacement": 3}},
        {"site": loud["site"], "outcome": Outcome.DROP_TOO_LOUD, "failing_tests": ["t"] * 41},
    ]

    rows = fn_api.build_stream([{"results": [gap, timeout, kept]}, {"results": [loud, pending]}], scored)

    assert [(r["verdict"], r["location"], r["tests_red"], r["detail"]) for r in rows] == [
        ("gap", "tenacity/wait.py:88", 0, "test gap → report"),
        ("drop", "tenacity/stop.py:10", None, "timeout"),
        ("keep", fn_api.MASKED_LOCATION, 1, "displacement 3"),
        ("drop", "tenacity/__init__.py:412", 41, "too loud"),
        ("scoring", fn_api.MASKED_LOCATION, 2, "full suite…"),
    ]
    serialized = json.dumps(rows)
    assert "retry.py" not in serialized and ":45" not in serialized
    assert "before.py" not in serialized


def test_stream_row_ids_are_stable_and_opaque():
    record = {"site": _site(), "outcome": Outcome.TEST_GAP}
    first = fn_api.build_stream([{"results": [record]}], [])[0]["id"]
    assert first == fn_api.build_stream([{"results": [record]}], [])[0]["id"]
    assert "retry" not in first and "45" not in first


def test_stream_unreproduced_survivor_is_a_drop_not_a_gap():
    survivor = {"site": _site(), "outcome": "SURVIVOR", "targeted_failures": ["t"]}
    scored = [{"site": _site(), "outcome": Outcome.TEST_GAP}]
    (row,) = fn_api.build_stream([{"results": [survivor]}], scored)
    assert (row["verdict"], row["detail"]) == ("drop", "not reproducible")


class _FakeSfn:
    class exceptions:
        class ExecutionDoesNotExist(Exception):
            pass

    def __init__(self, execution=None):
        self.execution = execution
        self.started = []

    def describe_execution(self, executionArn):
        if self.execution is None:
            raise self.exceptions.ExecutionDoesNotExist()
        return self.execution

    def start_execution(self, **kwargs):
        self.started.append(kwargs)
        return {"executionArn": "arn:exec"}


def test_get_forge_reports_phase_counts_and_summary(env, monkeypatch):
    exec_id = "forge-abc"
    prefix = f"answers/_work/{exec_id}/"
    store = {
        f"{prefix}baseline.json": {"total_tests": 183, "line_to_tests": {"a:1": ["t"], "a:2": ["t"]}},
        f"{prefix}batches/batch-0000.json": {"sites": [_site(), _site("tenacity/wait.py", 88)]},
        f"{prefix}raw/batch-0000.json": {
            "results": [
                {"site": _site(), "outcome": "SURVIVOR", "targeted_failures": ["t"]},
                {"site": _site("tenacity/wait.py", 88), "outcome": Outcome.TEST_GAP},
            ]
        },
        f"{prefix}scored.json": {
            "scored": [
                {"site": _site(), "outcome": Outcome.ADMITTED, "failing_tests": ["t"],
                 "score_breakdown": {"displacement": 2}}
            ]
        },
    }
    monkeypatch.setattr(fn_api.s3_io, "list_keys", lambda b, p: [k for k in store if k.startswith(p)])
    monkeypatch.setattr(fn_api.s3_io, "get_json", lambda b, k: store[k])
    fake = _FakeSfn(
        {
            "status": "SUCCEEDED",
            "startDate": datetime(2026, 9, 15, tzinfo=timezone.utc),
            "input": json.dumps({"repo_url": "https://github.com/jd/tenacity"}),
            "output": json.dumps({"challenges_written": 1, "gaps_written": 1, "repo": "jd__tenacity"}),
        }
    )
    monkeypatch.setattr(fn_api, "sfn", lambda: fake)

    body = _body(fn_api.get_forge(exec_id))

    assert body["phase"] == "done"
    assert body["baseline"] == {"total_tests": 183, "covered_lines": 2}
    assert (body["candidates"], body["batches"], body["batches_done"]) == (2, 1, 1)
    assert body["counts"] == {"keep": 1, "drop": 0, "gap": 1, "scoring": 0}
    assert body["summary"] == {"challenges_ready": 1, "test_gaps": 1, "repo": "jd__tenacity"}


def test_get_forge_unknown_execution_is_404(env, monkeypatch):
    monkeypatch.setattr(fn_api, "sfn", lambda: _FakeSfn(None))
    assert fn_api.get_forge("forge-nope")["statusCode"] == 404


@pytest.mark.parametrize(
    "status, baseline, batches, raw, scored, phase",
    [
        ("RUNNING", False, 0, 0, False, "baseline"),
        ("RUNNING", True, 0, 0, False, "generate"),
        ("RUNNING", True, 4, 2, False, "run"),
        ("RUNNING", True, 4, 4, False, "score"),
        ("RUNNING", True, 4, 4, True, "package"),
        ("FAILED", True, 4, 4, False, "failed"),
    ],
)
def test_phase(status, baseline, batches, raw, scored, phase):
    assert fn_api._phase(status, baseline, batches, raw, scored) == phase


# --------------------------------------------------------------------------
# forge refusal
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url", ["https://github.com/pallets/click", "https://github.com/jd/tenacity-fork"])
def test_post_forge_refuses_unvetted_repo_without_starting_anything(env, monkeypatch, url):
    fake = _FakeSfn()
    monkeypatch.setattr(fn_api, "sfn", lambda: fake)

    response = fn_api.post_forge({"body": json.dumps({"repo_url": url})})

    assert response["statusCode"] == 422
    assert _body(response)["forgeable"] == [{"repo": "jd__tenacity", "url": "https://github.com/jd/tenacity"}]
    assert fake.started == []


@pytest.mark.parametrize("url", ["https://github.com/jd/tenacity", "https://GitHub.com/JD/tenacity.git/"])
def test_post_forge_accepts_the_vetted_repo(env, monkeypatch, url):
    monkeypatch.setenv("STATE_MACHINE_ARN", "arn:sm")
    fake = _FakeSfn()
    monkeypatch.setattr(fn_api, "sfn", lambda: fake)

    response = fn_api.post_forge({"body": json.dumps({"repo_url": url})})

    assert response["statusCode"] == 202
    assert len(fake.started) == 1


# --------------------------------------------------------------------------
# browse projections
# --------------------------------------------------------------------------

ROW = {
    "challenge_id": "jd__tenacity-3e58094d3b-0123456789ab",
    "repo": "jd__tenacity",
    "repo_url": "https://github.com/jd/tenacity",
    "license": "Apache-2.0",
    "language": "Python",
    "commit_sha": "3e58094d3bc414975aad9eadf343a32bdb3b89b3",
    "title": "Retry Storm",
    "description": "A. B.",
    "difficulty_score": 6.2,
    "score_breakdown": {"displacement": 2, "search_space": 3, "noise": 0.005, "d": 0.5, "s": 0.15, "n": 0.8},
    "failing_tests": ["tests/test_tenacity.py::test_x"],
    "total_tests": 183,
    "tree_key": "public/x/tree.tar.gz",
    "traceback_key": "public/x/traceback.txt",
}

ANSWER_FIELDS = {"diff", "patch", "file_path", "path", "lineno", "operator", "operator_id",
                 "original_token", "mutated_token", "tree_key", "traceback_key"}


def test_get_challenge_exposes_the_card_and_no_answer_fields(env, monkeypatch):
    monkeypatch.setattr(fn_api.ddb_io, "get", lambda table, key: dict(ROW))
    body = _body(fn_api.get_challenge(ROW["challenge_id"]))

    assert not ANSWER_FIELDS & set(body)
    assert (body["repo"], body["license"], body["difficulty_label"]) == ("jd__tenacity", "Apache-2.0", "medium")
    assert body["breakdown"] == ROW["score_breakdown"]
    assert body["failing_tests"] == ROW["failing_tests"]


@pytest.mark.parametrize("score, label", [(3.0, "easy"), (4.99, "easy"), (5.0, "medium"), (7.0, "hard")])
def test_difficulty_label(score, label):
    assert fn_api.difficulty_label(score) == label


def test_histogram_bins_clamp_to_the_edges():
    assert fn_api.histogram([3.0, 3.9, 4.0, 6.5, 9.99, 10.0]) == [2, 1, 0, 1, 0, 0, 2]


class _FakeTable:
    def __init__(self, items):
        self.items = items

    def scan(self, **kwargs):
        return {"Items": self.items}


def test_get_repos_lists_histogram_gaps_and_forgeable(env, monkeypatch):
    tables = {
        "challenges": _FakeTable([dict(ROW), {**ROW, "challenge_id": "c2", "difficulty_score": 3.5}]),
        "gaps": _FakeTable([{"repo": "jd__tenacity"}] * 3),
    }
    monkeypatch.setattr(fn_api.ddb_io, "table", lambda name: tables[name])

    body = _body(fn_api.get_repos())

    (repo,) = body["repos"]
    assert (repo["challenge_count"], repo["gap_count"], repo["license"]) == (2, 3, "Apache-2.0")
    assert repo["histogram"] == [1, 0, 0, 1, 0, 0, 0]
    assert body["forgeable"][0]["repo"] == "jd__tenacity"


# --------------------------------------------------------------------------
# reveal
# --------------------------------------------------------------------------

def _reveal_env(monkeypatch, submission):
    rows = {"submissions": submission, "challenges": dict(ROW)}
    monkeypatch.setattr(fn_reveal.ddb_io, "get", lambda table, key: rows[table])
    objects = {
        f"answers/{ROW['challenge_id']}/reveal.json": {
            **_site(), "commit_sha": ROW["commit_sha"],
            "original_line": "        return retry_all(*other.retries, self)",
            "mutated_line": "        return None",
        },
        f"answers/{ROW['challenge_id']}/mutation.patch": "--- a/tenacity/retry.py\n+++ b/tenacity/retry.py\n",
    }
    monkeypatch.setattr(fn_reveal.s3_io, "get_json", lambda b, k: objects[k])
    monkeypatch.setattr(fn_reveal.s3_io, "get_text", lambda b, k: objects[k])


def test_reveal_refuses_a_submission_that_did_not_pass(env, monkeypatch):
    for submission in (
        {"submission_id": "s", "challenge_id": ROW["challenge_id"], "status": "PENDING"},
        {"submission_id": "s", "challenge_id": ROW["challenge_id"], "status": "COMPLETE", "verdict": "FAIL"},
        {"submission_id": "s", "challenge_id": ROW["challenge_id"], "status": "COMPLETE", "verdict": "REJECTED"},
    ):
        _reveal_env(monkeypatch, submission)
        result = fn_reveal.handler({"submission_id": "s"}, None)
        assert result["status"] == 403
        assert "diff" not in result["body"]


def test_reveal_unknown_submission_is_404(env, monkeypatch):
    _reveal_env(monkeypatch, None)
    assert fn_reveal.handler({"submission_id": "nope"}, None)["status"] == 404


def test_reveal_returns_the_mutation_for_a_pass(env, monkeypatch):
    _reveal_env(monkeypatch, {"submission_id": "s", "challenge_id": ROW["challenge_id"],
                              "status": "COMPLETE", "verdict": "PASS", "tests_passed": 183})
    body = fn_reveal.handler({"submission_id": "s"}, None)["body"]
    assert body["diff"].startswith("--- a/tenacity/retry.py")
    assert (body["file_path"], body["lineno"], body["mutated_token"]) == ("tenacity/retry.py", 45, "None")
    assert body["github_url"] == (
        "https://github.com/jd/tenacity/blob/3e58094d3bc414975aad9eadf343a32bdb3b89b3/tenacity/retry.py#L45"
    )


def test_api_relays_the_reveal_status(env, monkeypatch):
    monkeypatch.setenv("REVEAL_FUNCTION_ARN", "arn:reveal")
    payload = {"status": 403, "body": {"error": "nope"}}

    class _Lambda:
        def invoke(self, **kwargs):
            assert kwargs["InvocationType"] == "RequestResponse"
            return {"Payload": io.BytesIO(json.dumps(payload).encode())}

    monkeypatch.setattr(fn_api, "lambda_client", lambda: _Lambda())
    response = fn_api.handler(
        {"routeKey": "GET /submissions/{submission_id}/reveal", "pathParameters": {"submission_id": "s"}},
        None,
    )
    assert response["statusCode"] == 403


# --------------------------------------------------------------------------
# ids
# --------------------------------------------------------------------------

def test_challenge_id_is_stable_and_does_not_name_the_file_or_line():
    cid = ids.challenge_id("jd__tenacity", "3e58094d3bc414975aad9eadf343a32bdb3b89b3",
                           "tenacity/retry.py", 45, "RETURN", "None")
    assert cid == ids.challenge_id("jd__tenacity", "3e58094d3bc414975aad9eadf343a32bdb3b89b3",
                                   "tenacity/retry.py", 45, "RETURN", "None")
    assert cid.startswith("jd__tenacity-3e58094d3b-")
    digest = cid.removeprefix("jd__tenacity-3e58094d3b-")
    assert len(digest) == 12 and all(c in "0123456789abcdef" for c in digest)
    assert "retry" not in cid and "L45" not in cid
    assert cid != ids.challenge_id("jd__tenacity", "3e58094d3bc414975aad9eadf343a32bdb3b89b3",
                                   "tenacity/retry.py", 45, "RETURN", "False")


# --------------------------------------------------------------------------
# investigation log (Phase 7)
# --------------------------------------------------------------------------

def test_sanitize_investigation_keeps_plausible_visits_in_time_order():
    visits = fn_api.sanitize_investigation(
        [
            {"path": "tenacity/retry.py", "at": 200},
            {"path": "tests/test_retry.py", "at": 100.7},
        ]
    )
    assert visits == [
        {"path": "tests/test_retry.py", "at": 100},
        {"path": "tenacity/retry.py", "at": 200},
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "not a list",
        None,
        [{"path": "", "at": 1}],
        [{"path": "x" * 201, "at": 1}],
        [{"path": "a.py"}],
        [{"path": "a.py", "at": "soon"}],
        [{"path": "a.py", "at": True}],
        [{"at": 1}],
        ["a.py"],
    ],
)
def test_sanitize_investigation_drops_anything_malformed(raw):
    assert fn_api.sanitize_investigation(raw) == []


def test_sanitize_investigation_caps_the_length():
    assert len(fn_api.sanitize_investigation([{"path": "a.py", "at": i} for i in range(500)])) == 300


def test_post_submission_stores_the_investigation_log(env, signed_in, monkeypatch):
    monkeypatch.setenv("GRADE_FUNCTION_ARN", "arn:grade")
    monkeypatch.setattr(fn_api.ddb_io, "get", lambda table, key: dict(ROW))
    stored = {}
    monkeypatch.setattr(fn_api.ddb_io, "put", lambda table, item: stored.update(item))

    class _Lambda:
        def invoke(self, **kwargs):
            return {}

    monkeypatch.setattr(fn_api, "lambda_client", lambda: _Lambda())

    response = fn_api.post_submission(
        signed_in(
            body=json.dumps(
                {
                    "challenge_id": ROW["challenge_id"],
                    "patch": "--- a/x\n+++ b/x\n",
                    "investigation": [{"path": "tenacity/retry.py", "at": 5}, {"bad": 1}],
                }
            )
        )
    )

    assert response["statusCode"] == 202
    assert stored["investigation"] == [{"path": "tenacity/retry.py", "at": 5}]
    # the log is display data: it must never reach the grader
    assert "investigation" not in json.loads(response["body"])


def test_post_submission_without_a_log_stores_no_field(env, signed_in, monkeypatch):
    monkeypatch.setenv("GRADE_FUNCTION_ARN", "arn:grade")
    monkeypatch.setattr(fn_api.ddb_io, "get", lambda table, key: dict(ROW))
    stored = {}
    monkeypatch.setattr(fn_api.ddb_io, "put", lambda table, item: stored.update(item))
    monkeypatch.setattr(fn_api, "lambda_client", lambda: type("L", (), {"invoke": lambda self, **k: {}})())

    fn_api.post_submission(
        {"body": json.dumps({"challenge_id": ROW["challenge_id"], "patch": "--- a/x\n+++ b/x\n"})}
    )
    assert "investigation" not in stored
