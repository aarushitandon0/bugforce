"""
Unit tests for bugforge.mutate. These matter more than most tests in this
project: a silent bug in token location or the surgical splice produces a
broken challenge (wrong line mutated, corrupted file, off-by-one byte offset
on a non-ASCII line) that nobody notices until someone tries to solve it.
"""
from __future__ import annotations

import ast
from dataclasses import replace

import pytest

from bugforge.mutate import MutationError, apply, find_candidates
from bugforge.models import MutationSite


def _revert(site: MutationSite) -> MutationSite:
    """Builds the inverse mutation: swap tokens, recompute col_end from the
    mutated token's own byte length (which may differ from the original's)."""
    new_col_end = site.col_start + len(site.mutated_token.encode("utf-8"))
    return replace(
        site,
        col_start=site.col_start,
        col_end=new_col_end,
        original_token=site.mutated_token,
        mutated_token=site.original_token,
    )


def _sites_for(source: str, operator_id: str, path: str = "pkg/mod.py") -> list[MutationSite]:
    return [s for s in find_candidates(source, path) if s.operator_id == operator_id]


def _assert_round_trip(source: str, site: MutationSite) -> None:
    mutated = apply(source, site)
    assert mutated != source
    reverted = apply(mutated, _revert(site))
    assert reverted == source


# --------------------------------------------------------------------------
# Per-operator: located, applied, and round-trips back to the original.
# --------------------------------------------------------------------------

def test_comparison_operators_round_trip():
    source = (
        "def check(a, b):\n"
        "    if a < b:\n"
        "        return 1\n"
        "    return 0\n"
    )
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 1
    site = sites[0]
    assert site.original_token == "<"
    assert site.mutated_token == "<="
    assert site.enclosing_function_name == "check"
    mutated = apply(source, site)
    assert "a <= b" in mutated
    _assert_round_trip(source, site)


@pytest.mark.parametrize(
    "op,expected_original,expected_mutated",
    [("<", "<", "<="), (">", ">", ">="), ("==", "==", "!="), ("<=", "<=", "<"), (">=", ">=", ">")],
)
def test_all_comparison_variants(op, expected_original, expected_mutated):
    source = f"def check(a, b):\n    return a {op} b\n"
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 1
    assert sites[0].original_token == expected_original
    assert sites[0].mutated_token == expected_mutated
    _assert_round_trip(source, sites[0])


def test_arithmetic_operators_round_trip():
    source = "def total(a, b, c):\n    return a + b * c\n"
    plus_sites = _sites_for(source, "ARITHMETIC")
    # both '+' and '*' should be found
    tokens = {s.original_token for s in plus_sites}
    assert tokens == {"+", "*"}
    for site in plus_sites:
        _assert_round_trip(source, site)
    mult_site = next(s for s in plus_sites if s.original_token == "*")
    assert mult_site.mutated_token == "//"


def test_boolean_operator_round_trip():
    source = "def ok(a, b):\n    return a and b\n"
    sites = _sites_for(source, "BOOLEAN")
    assert len(sites) == 1
    assert sites[0].original_token == "and"
    assert sites[0].mutated_token == "or"
    _assert_round_trip(source, sites[0])


def test_boundary_mutations_only_in_slice_range_comparison():
    source = (
        "def f(xs):\n"
        "    a = xs[1:2]\n"
        "    for i in range(3):\n"
        "        pass\n"
        "    if len(xs) > 4:\n"
        "        pass\n"
        "    unrelated = 5\n"
        "    return a\n"
    )
    sites = _sites_for(source, "BOUNDARY")
    original_values = sorted(int(s.original_token) for s in sites)
    # 1, 2 (slice), 3 (range), 4 (comparison) each appear once per +1/-1 -> two sites each
    assert original_values.count(1) == 2
    assert original_values.count(2) == 2
    assert original_values.count(3) == 2
    assert original_values.count(4) == 2
    # the unrelated literal 5 (plain assignment) must NOT be mutated
    assert 5 not in original_values
    for site in sites:
        _assert_round_trip(source, site)


def test_negation_round_trip():
    source = "def f(x):\n    if not x:\n        return 1\n    return 2\n"
    sites = _sites_for(source, "NEGATION")
    assert len(sites) == 1
    mutated = apply(source, sites[0])
    assert "    if x:\n" in mutated
    _assert_round_trip(source, sites[0])


def test_negation_skips_parenthesized_operand():
    # "not (" -- deleting only "not (" would leave the matching ")" dangling
    # and produce invalid syntax (caught live against tenacity's own source).
    source = "def f(a, b):\n    if not (a or b):\n        return 1\n    return 2\n"
    assert _sites_for(source, "NEGATION") == []


def test_return_true_becomes_false():
    source = "def f():\n    return True\n"
    sites = _sites_for(source, "RETURN")
    assert len(sites) == 1
    assert sites[0].original_token == "True"
    assert sites[0].mutated_token == "False"
    _assert_round_trip(source, sites[0])


def test_return_expr_becomes_none():
    source = "def f(x):\n    return x + 1\n"
    sites = _sites_for(source, "RETURN")
    assert len(sites) == 1
    assert sites[0].mutated_token == "None"
    _assert_round_trip(source, sites[0])


def test_return_bare_is_not_mutated():
    source = "def f():\n    return\n"
    assert _sites_for(source, "RETURN") == []


def test_default_arg_int_and_bool():
    source = "def f(x=3, flag=True):\n    return x\n"
    sites = _sites_for(source, "DEFAULT_ARG")
    tokens = {(s.original_token, s.mutated_token) for s in sites}
    assert ("3", "4") in tokens
    assert ("True", "False") in tokens
    for site in sites:
        _assert_round_trip(source, site)


# --------------------------------------------------------------------------
# Filtering rules
# --------------------------------------------------------------------------

def test_skips_test_files():
    source = "def check(a, b):\n    return a < b\n"
    assert find_candidates(source, "tests/test_thing.py") == []
    assert find_candidates(source, "pkg/test_thing.py") == []
    assert find_candidates(source, "conftest.py") == []
    assert find_candidates(source, "setup.py") == []
    assert find_candidates(source, "docs/example.py") == []


def test_skips_type_checking_block():
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    def helper(a, b):\n"
        "        return a < b\n"
    )
    assert find_candidates(source, "pkg/mod.py") == []


def test_skips_repr_and_str():
    source = (
        "class Foo:\n"
        "    def __repr__(self):\n"
        "        return 'x' if 1 < 2 else 'y'\n"
        "    def __str__(self):\n"
        "        return 'x' if 3 < 4 else 'y'\n"
        "    def normal(self):\n"
        "        return 'x' if 5 < 6 else 'y'\n"
)
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 1
    assert sites[0].enclosing_function_name == "normal"


def test_caps_candidates_per_file():
    lines = ["def f(x):"]
    for i in range(60):
        lines.append(f"    if x {'<' if i % 2 == 0 else '>'} {i}:")
        lines.append("        pass")
    lines.append("    return x")
    source = "\n".join(lines) + "\n"
    from bugforge.mutate import MAX_CANDIDATES_PER_FILE

    sites = find_candidates(source, "pkg/mod.py")
    assert len(sites) <= MAX_CANDIDATES_PER_FILE


# --------------------------------------------------------------------------
# Surgical-edit safety: byte-identical outside the mutated span
# --------------------------------------------------------------------------

def test_byte_identical_outside_mutated_span_with_comments_and_unicode():
    source = (
        "# a comment up top\n"
        "\n"
        "def greet(name):\n"
        "    # café is not ascii\n"
        "    label = \"héllo wörld\"  # non-ascii comment too\n"
        "\n"
        "    if len(name) > 3:\n"
        "        return label\n"
        "    return None\n"
    )
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 1
    site = sites[0]
    mutated = apply(source, site)

    orig_lines = source.splitlines(keepends=True)
    mut_lines = mutated.splitlines(keepends=True)
    assert len(orig_lines) == len(mut_lines)
    diffs = [i for i, (a, b) in enumerate(zip(orig_lines, mut_lines)) if a != b]
    assert diffs == [site.lineno - 1]
    assert "héllo wörld" in mutated  # unicode line untouched
    assert "café" in mutated
    assert mut_lines[site.lineno - 1] == "    if len(name) >= 3:\n"


def test_non_ascii_before_mutation_site_on_same_line():
    # the mutated token sits after multi-byte characters on its own line --
    # this is exactly the case that breaks under character-offset math.
    source = 'def f(x):\n    label = "café"; ok = 1 < 2\n    return label, ok\n'
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 1
    mutated = apply(source, sites[0])
    assert 'label = "café"; ok = 1 <= 2' in mutated
    _assert_round_trip(source, sites[0])


def test_nested_fstring_expression_mutates_without_corrupting_the_string():
    # Python 3.12+ tracks real source positions for expressions embedded in
    # f-strings, so `a > 0` inside the f-string is a legitimate, locatable
    # site. The risk isn't that it gets found -- it's that surgical byte
    # splicing inside the f-string's own line corrupts the string around it.
    source = (
        "def f(a, b, name):\n"
        "    msg = f\"{name} has {a + b} items: {'yes' if a > 0 else 'no'}\"\n"
        "    if a < b:\n"
        "        return msg\n"
        "    return msg\n"
    )
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 2
    fstring_site = next(s for s in sites if s.lineno == 2)
    toplevel_site = next(s for s in sites if s.lineno == 3)

    mutated = apply(source, fstring_site)
    assert ast.parse(mutated)  # still valid Python, f-string not corrupted
    lines = mutated.splitlines(keepends=True)
    assert lines[1] == "    msg = f\"{name} has {a + b} items: {'yes' if a >= 0 else 'no'}\"\n"
    assert lines[2] == source.splitlines(keepends=True)[2]  # `if a < b:` untouched
    _assert_round_trip(source, fstring_site)
    _assert_round_trip(source, toplevel_site)


def test_crlf_line_endings_survive():
    source = "def check(a, b):\r\n    if a < b:\r\n        return 1\r\n    return 0\r\n"
    sites = _sites_for(source, "COMPARISON")
    assert len(sites) == 1
    mutated = apply(source, sites[0])
    assert "\r\n" in mutated
    assert mutated.count("\r\n") == source.count("\r\n")
    assert "    if a <= b:\r\n" in mutated
    # every untouched line, including its \r\n, is byte-identical
    orig_lines = source.splitlines(keepends=True)
    mut_lines = mutated.splitlines(keepends=True)
    for i, (a, b) in enumerate(zip(orig_lines, mut_lines)):
        if i != sites[0].lineno - 1:
            assert a == b


# --------------------------------------------------------------------------
# apply() safety assertions
# --------------------------------------------------------------------------

def test_apply_rejects_stale_site_after_source_drift():
    source = "def check(a, b):\n    return a < b\n"
    sites = _sites_for(source, "COMPARISON")
    site = sites[0]
    drifted_source = "def check(a, b):\n    return a == b\n"  # '<' no longer at that offset
    with pytest.raises(MutationError):
        apply(drifted_source, site)


def test_apply_rejects_multiline_mutated_token():
    source = "def check(a, b):\n    return a < b\n"
    site = _sites_for(source, "COMPARISON")[0]
    bad_site = replace(site, mutated_token="<=\n")
    with pytest.raises(MutationError):
        apply(source, bad_site)
