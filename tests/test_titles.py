"""Template titles have to be distinct on a challenge grid.

16 of 20 tenacity challenges are RETURN mutations, so a title built from the
module basename produced "Return in retry" six times in a row. These cover the
two rules that fixed it: name the enclosing function, and fall back to the
enclosing class when that function is a dunder or a private helper.
"""
from __future__ import annotations

from bugforge.models import MutationSite
from cloud.describe import (
    DescribeInput,
    build_input,
    disambiguate_titles,
    fallback_description,
    title_subject,
)

SOURCE = '''"""Retry strategies."""


class retry_if_exception_type:
    def __call__(self, retry_state):
        return isinstance(retry_state.outcome.exception(), self.types)


def retry_always(retry_state):
    return True
'''

TRACEBACK = "E       AssertionError: assert True == False\n"
TEST_ID = "tests/test_retry.py::test_retry_if_exception_type"


def _site(fn: str | None, cls: str | None, lineno: int = 5) -> MutationSite:
    return MutationSite(
        path="tenacity/retry.py",
        lineno=lineno,
        col_start=0,
        col_end=1,
        operator_id="RETURN",
        original_token="x",
        mutated_token="None",
        enclosing_function_name=fn,
        enclosing_class_name=cls,
    )


def _input(fn: str | None, cls: str | None) -> DescribeInput:
    return build_input(_site(fn, cls), SOURCE, TEST_ID, TRACEBACK)


def test_named_function_wins():
    assert title_subject(_input("retry_always", None)) == "retry_always"


def test_dunder_falls_back_to_the_class():
    """"Return in __call__" names nothing a learner can recognise."""
    assert title_subject(_input("__call__", "retry_if_exception_type")) == "retry_if_exception_type"


def test_private_helper_falls_back_to_the_class():
    assert title_subject(_input("_check", "retry_base")) == "retry_base"


def test_dunder_without_a_class_keeps_the_function():
    """A module-level dunder has no class to fall back to."""
    assert title_subject(_input("__getattr__", None)) == "__getattr__"


def test_module_level_mutation_falls_back_to_the_module_basename():
    assert title_subject(_input(None, None)) == "retry"


def test_class_body_mutation_outside_any_function_uses_the_class():
    assert title_subject(_input(None, "wait_exponential")) == "wait_exponential"


def test_title_uses_the_subject_and_the_operator_label():
    copy = fallback_description(_input("__call__", "retry_if_exception_type"))
    assert copy.title == "Return in retry_if_exception_type"
    assert copy.source == "template"


def test_title_is_deterministic():
    first = fallback_description(_input("__call__", "retry_if_exception_type"))
    second = fallback_description(_input("__call__", "retry_if_exception_type"))
    assert first == second


# ---------------------------------------------------------------------------
# cross-challenge disambiguation
# ---------------------------------------------------------------------------

def test_unique_titles_are_left_alone():
    titles = ["Return in retry_any", "Boolean in retry_all"]
    assert disambiguate_titles(titles, [10, 20]) == titles


def test_colliding_titles_get_their_line_number():
    """Two returns in one function still collide; line number breaks the tie."""
    assert disambiguate_titles(
        ["Return in retry_base", "Return in retry_base", "Boolean in wait_base"],
        [41, 58, 12],
    ) == ["Return in retry_base:41", "Return in retry_base:58", "Boolean in wait_base"]


def test_disambiguation_makes_every_title_unique():
    titles = ["Return in a", "Return in a", "Return in a", "Return in b"]
    out = disambiguate_titles(titles, [1, 2, 3, 4])
    assert len(set(out)) == len(out)


def test_disambiguation_is_order_preserving():
    out = disambiguate_titles(["B", "A", "B"], [7, 8, 9])
    assert out == ["B:7", "A", "B:9"]
