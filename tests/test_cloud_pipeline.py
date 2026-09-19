"""Unit tests for the Phase 4 lambdas' own logic.

Everything AWS is stubbed: these exercise the decisions the handlers make,
not boto3. The two that matter most:

  * fn_generate must only ever emit candidates on covered lines, in batches of
    exactly BATCH_SIZE, or the Map fans out over the wrong work.
  * fn_run_batch must write its raw results to S3 BEFORE raising on an
    empty batch. If that order ever flips, every test gap found in a
    no-survivor batch is silently lost -- and no-survivor batches are the
    common case, so most of the gap report would vanish without any error.
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest

from bugforge.models import Baseline, MutationSite
from bugforge.runner import RunResult
from bugforge.select import Outcome
from cloud import config, s3_io
from cloud.handlers import fn_generate, fn_run_batch
from cloud.handlers.fn_run_batch import SURVIVOR

SOURCE = """\
def compute(a, b):
    if a < b:
        return a + b
    return a - b


def other(x):
    if x > 10:
        return True
    return False
"""


class FakeS3:
    """An in-memory stand-in for the handful of s3_io calls the handlers make."""

    def __init__(self):
        self.objects: dict[str, str] = {}

    def put_json(self, bucket, key, payload):
        self.objects[key] = json.dumps(payload)
        return key

    def get_json(self, bucket, key):
        return json.loads(self.objects[key])

    def list_keys(self, bucket, prefix):
        return sorted(k for k in self.objects if k.startswith(prefix))


@pytest.fixture
def fake_s3(monkeypatch):
    fake = FakeS3()
    for module in (s3_io, fn_generate.s3_io, fn_run_batch.s3_io):
        monkeypatch.setattr(module, "put_json", fake.put_json)
        monkeypatch.setattr(module, "get_json", fake.get_json)
        monkeypatch.setattr(module, "list_keys", fake.list_keys)
    return fake


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("BUCKET", "test-bucket")
    monkeypatch.setenv("REPO_NAME", "acme__mathy")
    monkeypatch.setenv("REPO_URL", "https://github.com/acme/mathy")
    monkeypatch.setenv("REPO_PACKAGE", "pkg")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "mod.py").write_text(SOURCE, encoding="utf-8")
    monkeypatch.setattr(fn_generate.workspace, "repo_tree", lambda _: root)
    monkeypatch.setattr(fn_run_batch.workspace, "repo_tree", lambda _: root)
    monkeypatch.setattr(fn_run_batch.workspace, "python_exe", lambda: "python")
    # configure() rewrites the baseline cache dir to /tmp, which does not exist
    # on a Windows test host and is irrelevant here anyway.
    monkeypatch.setattr(fn_run_batch.workspace, "configure", lambda: None)
    return root


def _baseline(covered: dict[str, list[str]]) -> Baseline:
    return Baseline(
        repo="acme__mathy",
        commit_sha="deadbeefcafe1234",
        total_tests=100,
        green=True,
        line_to_tests=covered,
    )


def _site(lineno=2, operator="COMPARISON", original="<", mutated="<=", col=9):
    return MutationSite(
        path="pkg/mod.py",
        lineno=lineno,
        col_start=col,
        col_end=col + len(original),
        operator_id=operator,
        original_token=original,
        mutated_token=mutated,
        enclosing_function_name="compute",
    )


# --------------------------------------------------------------------------
# fn_generate
# --------------------------------------------------------------------------

def test_generate_only_emits_candidates_on_covered_lines(env, fake_s3, tree):
    # line 2 is covered; line 8 (`if x > 10`) is not
    baseline = _baseline({"pkg/mod.py:2": ["tests/test_mod.py::test_compute"]})
    fake_s3.put_json("b", "baseline.json", asdict(baseline))

    result = fn_generate.handler(
        {"execution_id": "exec-1", "baseline_key": "baseline.json"}, None
    )

    assert result["candidate_count"] > 0
    batch = fake_s3.get_json("b", result["batches"][0]["batch_key"])
    linenos = {site["lineno"] for site in batch["sites"]}
    assert linenos == {2}, "an uncovered line must never become a candidate"


def test_generate_chunks_into_batches_of_fifteen(env, fake_s3, tree, monkeypatch):
    # 40 covered sites -> 15 + 15 + 10
    sites = [asdict(_site(lineno=i)) for i in range(40)]
    monkeypatch.setattr(fn_generate.ADAPTER, "find_candidates", lambda *a: [])
    baseline = _baseline({})
    fake_s3.put_json("b", "baseline.json", asdict(baseline))

    real_handler_sites = sites  # inject after candidate discovery
    monkeypatch.setattr(
        fn_generate.ADAPTER,
        "find_candidates",
        lambda source, rel: [MutationSite(**s) for s in real_handler_sites],
    )
    monkeypatch.setattr(fn_generate.ADAPTER, "apply", lambda source, site: source)
    baseline.line_to_tests = {f"pkg/mod.py:{i}": ["t"] for i in range(40)}
    fake_s3.put_json("b", "baseline.json", asdict(baseline))

    result = fn_generate.handler(
        {"execution_id": "exec-1", "baseline_key": "baseline.json"}, None
    )

    assert result["candidate_count"] == 40
    assert result["batch_count"] == 3
    sizes = [
        len(fake_s3.get_json("b", b["batch_key"])["sites"]) for b in result["batches"]
    ]
    assert sizes == [fn_generate.BATCH_SIZE, fn_generate.BATCH_SIZE, 10]


def test_generate_handles_zero_candidates(env, fake_s3, tree):
    fake_s3.put_json("b", "baseline.json", asdict(_baseline({})))
    result = fn_generate.handler(
        {"execution_id": "exec-1", "baseline_key": "baseline.json"}, None
    )
    assert result["candidate_count"] == 0
    assert result["batch_count"] == 0
    assert result["batches"] == []


# --------------------------------------------------------------------------
# fn_run_batch
# --------------------------------------------------------------------------

def _prepare_batch(fake_s3, sites):
    baseline = _baseline({"pkg/mod.py:2": ["tests/test_mod.py::test_compute"]})
    fake_s3.put_json("b", "baseline.json", asdict(baseline))
    fake_s3.put_json(
        "b",
        "batch.json",
        {
            "execution_id": "exec-1",
            "batch_id": "batch-0000",
            "baseline_key": "baseline.json",
            "sites": [asdict(s) for s in sites],
        },
    )
    return {
        "execution_id": "exec-1",
        "batch_id": "batch-0000",
        "batch_key": "batch.json",
        "baseline_key": "baseline.json",
    }


def _run_result(**kwargs):
    defaults = dict(
        returncode=1,
        stdout="",
        stderr="",
        passed=0,
        failed=0,
        errors=0,
        timed_out=False,
        collection_error=False,
        failing_tests=[],
    )
    return RunResult(**{**defaults, **kwargs})


def test_run_batch_keeps_a_caught_mutation_as_a_survivor(env, fake_s3, tree, monkeypatch):
    monkeypatch.setattr(
        fn_run_batch,
        "run_mutation_with",
        lambda *a, **k: _run_result(failed=1, failing_tests=["tests/test_mod.py::test_compute"]),
    )
    event = _prepare_batch(fake_s3, [_site()])

    result = fn_run_batch.handler(event, None)

    assert result["survivor_count"] == 1
    raw = fake_s3.get_json("b", result["raw_key"])
    assert raw["results"][0]["outcome"] == SURVIVOR


def test_run_batch_classifies_an_uncaught_mutation_as_a_test_gap(env, fake_s3, tree, monkeypatch):
    monkeypatch.setattr(
        fn_run_batch, "run_mutation_with", lambda *a, **k: _run_result(returncode=0, passed=1)
    )
    event = _prepare_batch(fake_s3, [_site()])

    with pytest.raises(fn_run_batch.NoSurvivorsError):
        fn_run_batch.handler(event, None)

    raw = fake_s3.get_json("b", config.raw_result_key("exec-1", "batch-0000"))
    assert raw["results"][0]["outcome"] == Outcome.TEST_GAP


def test_run_batch_writes_raw_results_before_it_raises(env, fake_s3, tree, monkeypatch):
    """The invariant the whole gap report depends on.

    A batch with no survivors fails its Map branch by design, and Step
    Functions discards a failed branch's output -- so if the write happened
    after the raise, those test gaps would never reach fn_persist.
    """
    monkeypatch.setattr(
        fn_run_batch, "run_mutation_with", lambda *a, **k: _run_result(returncode=0, passed=1)
    )
    event = _prepare_batch(fake_s3, [_site(), _site(lineno=2, operator="ARITHMETIC")])

    with pytest.raises(fn_run_batch.NoSurvivorsError):
        fn_run_batch.handler(event, None)

    key = config.raw_result_key("exec-1", "batch-0000")
    assert key in fake_s3.objects, "raw results must be durable before the branch fails"
    assert len(fake_s3.get_json("b", key)["results"]) == 2


def test_run_batch_drops_a_timeout(env, fake_s3, tree, monkeypatch):
    monkeypatch.setattr(fn_run_batch, "run_mutation_with", lambda *a, **k: _run_result(timed_out=True))
    event = _prepare_batch(fake_s3, [_site()])

    with pytest.raises(fn_run_batch.NoSurvivorsError):
        fn_run_batch.handler(event, None)

    raw = fake_s3.get_json("b", config.raw_result_key("exec-1", "batch-0000"))
    assert raw["results"][0]["outcome"] == Outcome.DROP_TIMEOUT


def test_run_batch_drops_a_collection_error(env, fake_s3, tree, monkeypatch):
    monkeypatch.setattr(
        fn_run_batch,
        "run_mutation_with",
        lambda *a, **k: _run_result(errors=1, collection_error=True),
    )
    event = _prepare_batch(fake_s3, [_site()])

    with pytest.raises(fn_run_batch.NoSurvivorsError):
        fn_run_batch.handler(event, None)

    raw = fake_s3.get_json("b", config.raw_result_key("exec-1", "batch-0000"))
    assert raw["results"][0]["outcome"] == Outcome.DROP_CATASTROPHIC


def test_run_batch_never_ships_survivor_bodies_through_the_state_machine(
    env, fake_s3, tree, monkeypatch
):
    # The Map's aggregate output has a 256KB ceiling; survivors stay in S3.
    monkeypatch.setattr(
        fn_run_batch,
        "run_mutation_with",
        lambda *a, **k: _run_result(failed=1, failing_tests=["tests/test_mod.py::test_compute"]),
    )
    event = _prepare_batch(fake_s3, [_site()])

    result = fn_run_batch.handler(event, None)

    assert "survivors" not in result
    assert set(result) == {
        "execution_id",
        "batch_id",
        "raw_key",
        "survivor_count",
        "gap_count",
    }
