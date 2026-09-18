"""Unit tests for the Phase 5 describer.

The rule this file defends: the pipeline must produce complete, shippable copy
with Bedrock switched off, and model output that names anything from the
mutation site must never ship. If describe() can raise, can call the model
with the flag set, or can let an identifier through, the one model call has
become load-bearing -- which is exactly what the design forbids.
"""
from __future__ import annotations

import json
import sys

import pytest

from bugforge.models import MutationSite
from cloud import describe as describe_module
from cloud.describe import (
    DescribeInput,
    Rejected,
    build_input,
    check_output,
    describe,
    fallback_description,
    module_basename,
    parse_failure,
    scope_identifiers,
)

SOURCE = '''"""Stop strategies: decide when a retrying call should give up."""


class stop_after_attempt:
    def __init__(self, max_attempt_number):
        self.max_attempt_number = max_attempt_number

    def __call__(self, retry_state):
        return retry_state.attempt_number >= self.max_attempt_number


LIMIT = compute_limit(3)
'''

SITE = MutationSite(
    path="tenacity/stop.py",
    lineno=9,
    col_start=43,
    col_end=45,
    operator_id="COMPARISON",
    original_token=">=",
    mutated_token=">",
    enclosing_function_name="__call__",
    enclosing_class_name="stop_after_attempt",
)

TEST_ID = "tests/test_tenacity.py::TestStopConditions::test_stop_after_attempt"

PYTEST_TB = """
    def test_stop_after_attempt(self):
>       assert r.stop(make_retry_state(3, 6546)) == True
E       assert False == True
E        +  where False = stop(...)
"""

UNITTEST_TB = """
>       self.assertEqual(attempts, 3)
E       AssertionError: 4 != 3
"""

EXCEPTION_TB = """
>       return fn(*args)
E       TypeError: 'NoneType' object is not callable
"""

GOOD = json.dumps(
    {
        "title": "Retry Storm",
        "description": "A failed request keeps retrying instead of giving up after three "
        "attempts. Throughput collapses under load.",
    }
)


def _input(**overrides) -> DescribeInput:
    base = build_input(SITE, SOURCE, TEST_ID, PYTEST_TB)
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


# --------------------------------------------------------------------------
# deterministic inputs
# --------------------------------------------------------------------------

def test_parse_failure_pytest_equality():
    assert parse_failure(PYTEST_TB) == ("True", "False", "AssertionError")


def test_parse_failure_unittest_assert_equal():
    assert parse_failure(UNITTEST_TB) == ("3", "4", "AssertionError")


def test_parse_failure_exception_without_assertion():
    assert parse_failure(EXCEPTION_TB) == (None, None, "TypeError")


@pytest.mark.parametrize(
    "e_line, parsed",
    [
        # formats taken from real tenacity challenge tracebacks
        ("AssertionError: False is not true", ("True", "False", "AssertionError")),
        ("AssertionError: None is not false", ("False", "None", "AssertionError")),
        (
            "AssertionError: None is not an instance of <class 'tenacity.retry.retry_all'>",
            ("an instance of retry_all", "None", "AssertionError"),
        ),
        ("Failed: DID NOT RAISE ValueError", ("ValueError to be raised", "no exception", "Failed")),
        ("Failed: DID NOT RAISE <class 'ValueError'>", ("ValueError to be raised", "no exception", "Failed")),
        ("Exception", (None, None, "Exception")),
        ("tests.test_asyncio.T.test.<locals>.test.<locals>.CustomException", (None, None, "CustomException")),
    ],
)
def test_parse_failure_other_real_formats(e_line, parsed):
    assert parse_failure(f">       call()\nE       {e_line}\n") == parsed


def test_parse_failure_reports_the_innermost_exception_of_a_chain():
    tb = "E       NameError: Hi there\n>       raise OSError from e\nE       OSError\n"
    assert parse_failure(tb) == (None, None, "OSError")


def test_parse_failure_ignores_bare_constant_lines():
    assert parse_failure("E       assert x\nE       None\n")[2] == "AssertionError"


def test_parse_failure_empty_traceback():
    assert parse_failure("") == (None, None, None)


def test_parse_failure_clips_long_values():
    expected, actual, _ = parse_failure("E       assert " + "x" * 500 + " == 1\n")
    assert expected == "1"
    assert len(actual) == describe_module.MAX_VALUE_CHARS
    assert actual.endswith("...")


def test_module_basename():
    assert module_basename("tenacity/retry.py") == "retry"
    assert module_basename("tenacity/asyncio/__init__.py") == "asyncio"
    assert module_basename("tenacity\\wait.py") == "wait"


def test_scope_identifiers_cover_the_enclosing_function_and_class():
    names = scope_identifiers(SOURCE, 9)
    assert {"stop_after_attempt", "retry_state", "attempt_number", "max_attempt_number"} <= names
    assert "self" not in names  # not a leak, and would match ordinary English
    assert "__call__" in names


def test_scope_identifiers_for_a_module_level_line():
    assert scope_identifiers(SOURCE, 12) == {"LIMIT", "compute_limit"}


def test_build_input_forbids_module_path_and_test_id_parts():
    inp = build_input(SITE, SOURCE, TEST_ID, PYTEST_TB)
    assert {"stop", "tenacity", "test_tenacity", "TestStopConditions", "test_stop_after_attempt"} <= (
        inp.forbidden_identifiers
    )
    assert inp.module_docstring.startswith("Stop strategies")
    assert inp.test_name == "test_stop_after_attempt"


# --------------------------------------------------------------------------
# fallback
# --------------------------------------------------------------------------

def test_fallback_matches_the_spec_template():
    inp = build_input(
        MutationSite("tenacity/retry.py", 1, 0, 1, "BOUNDARY", "3", "4", None),
        "x = 1\n",
        "tests/test_retry.py::test_attempts",
        UNITTEST_TB,
    )
    result = fallback_description(inp)
    assert result.title == "Boundary in retry"
    assert result.description == "test_attempts expected 3, got 4."
    assert result.source == "template"


def test_fallback_names_the_exception_when_there_is_no_equality():
    inp = build_input(SITE, SOURCE, TEST_ID, EXCEPTION_TB)
    assert fallback_description(inp).description == "test_stop_after_attempt raised TypeError."


def test_fallback_is_deterministic_for_every_operator():
    for op in describe_module.OPERATOR_LABELS:
        first = fallback_description(_input(operator_id=op))
        second = fallback_description(_input(operator_id=op))
        assert first == second
        # __call__ names nothing on its own, so the title falls back to the
        # class that owns it.
        assert first.title.endswith(" in stop_after_attempt")


# --------------------------------------------------------------------------
# hard post-check
# --------------------------------------------------------------------------

def test_check_output_accepts_the_spec_example():
    title, description = check_output(GOOD, _input(forbidden_identifiers=set()))
    assert title == "Retry Storm"


@pytest.mark.parametrize(
    "text, reason",
    [
        ("```json\n" + GOOD + "\n```", "not valid JSON"),
        ("Here you go: " + GOOD, "not valid JSON"),
        (json.dumps({"title": "Retry Storm", "description": "A. B.", "extra": 1}), "exactly the keys"),
        (json.dumps({"title": "Storm", "description": "It broke. It hurts."}), "2-3 words"),
        (json.dumps({"title": "A Very Long Storm", "description": "It broke. It hurts."}), "2-3 words"),
        (json.dumps({"title": "Retry Storm", "description": "It broke."}), "exactly 2 sentences"),
        (json.dumps({"title": "Retry Storm", "description": "It broke. It hurts. Badly."}), "exactly 2 sentences"),
        (json.dumps({"title": "Retry Storm", "description": "It broke. It hurts"}), "exactly 2 sentences"),
        (json.dumps({"title": "Retry Storm", "description": "Look in wait.py first. It hurts."}), "file path"),
        (json.dumps({"title": "Retry Storm", "description": "See line 42 for it. It hurts."}), "line number"),
        (json.dumps({"title": "Retry Storm", "description": "Calling reset() does nothing. It hurts."}), "code-shaped"),
        (json.dumps({"title": "Retry Storm", "description": "The give_up flag is ignored. It hurts."}), "code-shaped"),
    ],
)
def test_check_output_rejects(text, reason):
    with pytest.raises(Rejected, match=reason):
        check_output(text, _input(forbidden_identifiers=set()))


def test_check_output_rejects_scope_identifiers_case_insensitively():
    text = json.dumps({"title": "Early Stop", "description": "Calls end too soon. Nobody retries."})
    with pytest.raises(Rejected, match="'stop'"):
        check_output(text, _input())


def test_check_output_matches_whole_words_only():
    # "stopped" is not the identifier "stop"
    text = json.dumps({"title": "Premature Halt", "description": "Calls stopped too soon. Work is lost."})
    assert check_output(text, _input(forbidden_identifiers={"stop"}))[0] == "Premature Halt"


# --------------------------------------------------------------------------
# describe(): flag, retry-once, fallback
# --------------------------------------------------------------------------

class _FakeClient:
    """Returns queued responses and records every prompt it was sent."""

    def __init__(self, texts, stop_reason="end_turn"):
        self.texts = list(texts)
        self.prompts: list[str] = []
        self.stop_reason = stop_reason
        self.messages = self

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        block = type("Block", (), {"type": "text", "text": self.texts.pop(0)})()
        return type("Resp", (), {"content": [block], "stop_reason": self.stop_reason})()


@pytest.fixture
def fake_client(monkeypatch):
    def install(texts, stop_reason="end_turn"):
        client = _FakeClient(texts, stop_reason)
        monkeypatch.setattr(describe_module, "_client", lambda: client)
        return client

    monkeypatch.delenv(describe_module.DISABLE_ENV, raising=False)
    return install


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_disable_flag_never_touches_the_model(monkeypatch, value):
    monkeypatch.setenv(describe_module.DISABLE_ENV, value)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # any import would now raise
    calls = []
    monkeypatch.setattr(describe_module, "_client", lambda: calls.append(1))

    result = describe(_input())

    assert calls == []
    assert result == fallback_description(_input())


def test_describe_uses_model_output_that_passes(fake_client):
    client = fake_client([GOOD])
    result = describe(_input(forbidden_identifiers=set()))
    assert (result.source, result.title, result.rejections) == ("bedrock", "Retry Storm", [])
    assert len(client.prompts) == 1


def test_describe_retries_once_after_a_rejection(fake_client):
    client = fake_client(["not json", GOOD])
    result = describe(_input(forbidden_identifiers=set()))
    assert result.source == "bedrock"
    assert len(result.rejections) == 1
    assert "rejected (not valid JSON" in client.prompts[1]


def test_describe_falls_back_after_two_rejections(fake_client):
    client = fake_client(["not json", "still not json", GOOD])
    inp = _input()
    result = describe(inp)
    assert len(client.prompts) == 2, "retry exactly once, never a third call"
    assert result.source == "template"
    assert (result.title, result.description) == (
        fallback_description(inp).title,
        fallback_description(inp).description,
    )
    assert len(result.rejections) == 2


def test_describe_treats_a_refusal_as_a_rejection(fake_client):
    client = fake_client([GOOD, GOOD], stop_reason="refusal")
    result = describe(_input(forbidden_identifiers=set()))
    assert result.source == "template"
    assert len(client.prompts) == 2


def test_describe_falls_back_immediately_when_the_call_fails(monkeypatch):
    monkeypatch.delenv(describe_module.DISABLE_ENV, raising=False)
    calls = []

    def boom():
        calls.append(1)
        raise RuntimeError("no credentials")

    monkeypatch.setattr(describe_module, "_client", boom)
    result = describe(_input())
    assert result.source == "template"
    assert calls == [1]


def test_prompt_carries_the_inputs_but_not_the_module_path(fake_client):
    client = fake_client([GOOD])
    describe(_input(forbidden_identifiers=set()))
    prompt = client.prompts[0]
    assert "test_stop_after_attempt" in prompt
    assert "Expected: True" in prompt and "Actual: False" in prompt
    assert "Stop strategies" in prompt
    assert "tenacity/stop.py" not in prompt


# --------------------------------------------------------------------------
# fn_describe handler
# --------------------------------------------------------------------------

def test_handler_describes_admitted_records_with_bedrock_disabled(tmp_path, monkeypatch):
    from dataclasses import asdict

    from cloud.handlers import fn_describe

    (tmp_path / "tenacity").mkdir()
    (tmp_path / "tenacity" / "stop.py").write_text(SOURCE, encoding="utf-8")
    monkeypatch.setenv("BUCKET", "b")
    monkeypatch.setenv("REPO_DIR", str(tmp_path))
    monkeypatch.setenv(describe_module.DISABLE_ENV, "1")

    store = {
        "scored.json": {
            "execution_id": "e",
            "commit_sha": "abc",
            "scored": [
                {
                    "outcome": "ADMITTED",
                    "site": asdict(SITE),
                    "failing_tests": [TEST_ID],
                    "representative_test": TEST_ID,
                    "traceback": PYTEST_TB,
                },
                {"outcome": "DROP_too_loud", "site": asdict(SITE)},
            ],
        }
    }
    monkeypatch.setattr(fn_describe.s3_io, "get_json", lambda bucket, key: store[key])
    monkeypatch.setattr(
        fn_describe.s3_io, "put_json", lambda bucket, key, payload: store.__setitem__(key, payload) or key
    )
    context = type("Ctx", (), {"get_remaining_time_in_millis": lambda self: 300_000})()

    result = fn_describe.handler({"execution_id": "e", "scored_key": "scored.json"}, context)

    assert (result["bedrock_count"], result["template_count"]) == (0, 1)
    admitted, dropped = store[result["described_key"]]["scored"]
    assert admitted["title"] == "Comparison in stop_after_attempt"
    assert admitted["description"] == "test_stop_after_attempt expected True, got False."
    assert "title" not in dropped
