"""Tests for the Go LanguageAdapter.

Split into two halves on purpose:

* The pure-Python half -- path rules, coverage-profile parsing, `go test`
  output parsing, test-id grouping -- runs everywhere. These are the parts
  where a silent mistake is most expensive, because a misparsed profile does
  not crash, it just files every mutation as a test gap.
* The half that needs a real Go toolchain is skipped when `go` is not on
  PATH, rather than mocked. The whole point of the locator is that it agrees
  with the Go compiler about where a token is; asserting against a fake would
  test nothing.
"""
from __future__ import annotations

import shutil
import textwrap

import pytest

from bugforge.languages.go import (
    GoAdapter,
    parse_go_failure,
    _parse_go_test_output,
    _profile_to_lines,
    is_mutable_source_path,
)
from bugforge.models import MutationSite
from bugforge.mutate import MutationError

needs_go = pytest.mark.skipif(
    shutil.which("go") is None, reason="needs a Go toolchain to build the locator helper"
)

MODULE = "github.com/golang-jwt/jwt/v5"

SAMPLE = textwrap.dedent(
    """\
    package sample

    import "errors"

    type Limit struct{ max int }

    func (l *Limit) Allow(n int) bool {
    	if n > l.max {
    		return false
    	}
    	if !l.ready() && n > 0 {
    		return false
    	}
    	return true
    }

    func (l *Limit) ready() bool { return l.max != 0 }

    func (l *Limit) String() string {
    	if l.max > 10 {
    		return "big"
    	}
    	return "small"
    }

    func check(vals []int) error {
    	if len(vals) == 0 {
    		return errors.New("empty")
    	}
    	if vals[0] > 3 {
    		return errors.New("too big")
    	}
    	return nil
    }
    """
)


# --------------------------------------------------------------------------
# path rules
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,mutable",
    [
        ("parser.go", True),
        ("request/oauth2.go", True),
        ("cmd/jwt/main.go", True),
        ("parser_test.go", False),
        ("request/oauth2_test.go", False),
        ("vendor/x/y.go", False),
        ("testdata/fixture.go", False),
        ("test/helpers.go", False),
        ("tests/helpers.go", False),
        ("docs/example.go", False),
        ("README.md", False),
        ("go.mod", False),
    ],
)
def test_is_mutable_source_path(path, mutable):
    assert is_mutable_source_path(path) is mutable


def test_windows_separators_are_normalized():
    assert is_mutable_source_path("request\\oauth2.go") is True
    assert is_mutable_source_path("vendor\\x\\y.go") is False


# --------------------------------------------------------------------------
# coverage profiles
# --------------------------------------------------------------------------


def test_profile_expands_blocks_to_lines_and_strips_the_module_prefix():
    profile = textwrap.dedent(
        f"""\
        mode: set
        {MODULE}/parser.go:32.13,35.71 2 1
        {MODULE}/request/oauth2.go:10.2,10.20 1 1
        """
    )
    assert _profile_to_lines(profile, MODULE) == {
        "parser.go": {32, 33, 34, 35},
        "request/oauth2.go": {10},
    }


def test_profile_ignores_blocks_that_never_ran():
    profile = f"mode: set\n{MODULE}/parser.go:32.13,35.71 2 0\n"
    assert _profile_to_lines(profile, MODULE) == {}


def test_profile_ignores_files_outside_the_module():
    # -coverpkg can pull in a dependency. Its lines are not mutable and its
    # paths would not resolve against the repo tree, so they must not appear.
    profile = f"mode: set\ngithub.com/other/dep/x.go:1.1,2.2 1 1\n{MODULE}/a.go:5.1,5.9 1 1\n"
    assert _profile_to_lines(profile, MODULE) == {"a.go": {5}}


def test_profile_tolerates_a_header_only_file():
    assert _profile_to_lines("mode: set\n", MODULE) == {}
    assert _profile_to_lines("", MODULE) == {}


# --------------------------------------------------------------------------
# `go test` output
# --------------------------------------------------------------------------


def test_subtests_do_not_inflate_the_counts():
    """The denominator the scorer divides by has to be top-level tests.

    `go test -v` indents subtest results under their parent. Counting them
    would report dozens of results for a handful of tests and skew the noise
    term of the difficulty score.
    """
    output = textwrap.dedent(
        """\
        === RUN   TestVerifyAud
        --- FAIL: TestVerifyAud (0.00s)
            --- FAIL: TestVerifyAud/empty (0.00s)
            --- PASS: TestVerifyAud/match (0.00s)
        --- PASS: TestParse (0.01s)
        --- SKIP: TestSlow (0.00s)
        FAIL
        """
    )
    passed, failed, errors, failing = _parse_go_test_output(output)
    assert (passed, failed, errors) == (1, 1, 0)
    assert failing == ["TestVerifyAud"]


def test_a_failing_subtest_is_reported_under_its_parent():
    output = "--- FAIL: TestX (0.0s)\n    --- FAIL: TestX/case_one (0.0s)\nFAIL\n"
    _, _, _, failing = _parse_go_test_output(output)
    # The baseline knows TestX, not TestX/case_one, so the id must be the
    # parent's -- and must not be repeated once per failing subtest.
    assert failing == ["TestX"]


def test_a_build_failure_is_an_error_not_a_test_failure():
    output = (
        "# github.com/x/y\n"
        "./a.go:3:2: invalid operation: mismatched types\n"
        "FAIL\tgithub.com/x/y [build failed]\n"
    )
    passed, failed, errors, failing = _parse_go_test_output(output)
    assert (passed, failed, errors) == (0, 0, 1)
    assert failing == []


def test_a_green_run_reports_no_errors():
    passed, failed, errors, failing = _parse_go_test_output("--- PASS: TestA (0s)\nok\n")
    assert (passed, failed, errors, failing) == (1, 0, 0, [])


# --------------------------------------------------------------------------
# the locator
# --------------------------------------------------------------------------


@needs_go
def test_every_located_span_matches_the_token_it_claims():
    """The one invariant the whole byte-offset discipline exists to hold.

    If a span and its `original_token` ever disagree, `apply` splices over
    the wrong bytes -- so this checks every site in the sample, not one.
    """
    sites = GoAdapter().find_candidates(SAMPLE, "sample.go")
    assert sites, "expected the sample to yield candidates"
    lines = SAMPLE.splitlines()
    for site in sites:
        raw = lines[site.lineno - 1].encode("utf-8")
        assert raw[site.col_start : site.col_end].decode("utf-8") == site.original_token


@needs_go
def test_offsets_are_byte_offsets_not_character_offsets():
    """A non-ASCII character earlier on the line must shift the offset.

    This is the bug the module is written in bytes to avoid: with character
    offsets the span below lands two bytes early and silently corrupts the
    line instead of failing.
    """
    # The non-ASCII has to sit BEFORE the operator on the same line or the
    # test proves nothing: "µs — über" is 9 characters but 13 bytes, so a
    # character offset would point four bytes short of the ">".
    source = (
        "package x\n"
        "\n"
        "func f(n int) bool {\n"
        '\tif len("µs — über") > 3 {\n'
        "\t\treturn true\n"
        "\t}\n"
        "\treturn false\n"
        "}\n"
    )
    text_line = source.splitlines()[3]
    raw_line = text_line.encode("utf-8")
    assert len(raw_line) != len(text_line), "sample must actually be multi-byte"

    sites = GoAdapter().find_candidates(source, "x.go")
    site = next(s for s in sites if s.original_token == ">")
    assert raw_line[site.col_start : site.col_end] == b">"
    # And the offset really is the byte index, not the character index a
    # naive implementation would have produced.
    assert site.col_start == raw_line.index(b">")
    assert site.col_start != text_line.index(">")


@needs_go
def test_operators_found_in_the_sample():
    sites = GoAdapter().find_candidates(SAMPLE, "sample.go")
    found = {s.operator_id for s in sites}
    assert {"COMPARISON", "BOOLEAN", "NEGATION", "RETURN", "BOUNDARY"} <= found


@needs_go
def test_err_not_nil_is_mutable():
    """`if err != nil` is the defining control-flow decision in Go source.

    Python's operator table only mutates `==`; Go's adds `!=` because this is
    the shape almost every real Go defect takes.
    """
    source = "package x\n\nimport \"errors\"\n\nfunc f() error {\n\terr := errors.New(\"e\")\n\tif err != nil {\n\t\treturn err\n\t}\n\treturn nil\n}\n"
    sites = GoAdapter().find_candidates(source, "x.go")
    assert any(s.original_token == "!=" and s.mutated_token == "==" for s in sites)


@needs_go
def test_string_concatenation_is_not_treated_as_arithmetic():
    """`"a" + "b"` is a "+" that must not become "-": that is a compile
    error, not a defect, and would burn a full build to discover."""
    source = 'package x\n\nfunc f() string {\n\treturn "a" + "b"\n}\n'
    sites = GoAdapter().find_candidates(source, "x.go")
    assert not [s for s in sites if s.operator_id == "ARITHMETIC"]


@needs_go
def test_stringer_bodies_are_skipped():
    """Go's answer to __repr__/__str__. mutate.py skips those; so do we."""
    sites = GoAdapter().find_candidates(SAMPLE, "sample.go")
    assert not [s for s in sites if s.enclosing_function_name == "String"]


@needs_go
def test_sites_name_their_enclosing_function_and_receiver():
    sites = GoAdapter().find_candidates(SAMPLE, "sample.go")
    allow = [s for s in sites if s.enclosing_function_name == "Allow"]
    assert allow
    assert all(s.enclosing_class_name == "Limit" for s in allow)


@needs_go
def test_test_files_yield_nothing_even_if_asked_directly():
    assert GoAdapter().find_candidates(SAMPLE, "sample_test.go") == []


@needs_go
def test_unparseable_source_yields_no_candidates_rather_than_raising():
    assert GoAdapter().find_candidates("package x\n\nfunc (", "x.go") == []


@needs_go
def test_the_candidate_cap_is_the_same_as_pythons():
    from bugforge.mutate import MAX_CANDIDATES_PER_FILE

    body = "\n".join(f"\tif n > {i} {{ return false }}" for i in range(200))
    source = f"package x\n\nfunc f(n int) bool {{\n{body}\n\treturn true\n}}\n"
    sites = GoAdapter().find_candidates(source, "x.go")
    assert len(sites) == MAX_CANDIDATES_PER_FILE


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


@needs_go
def test_apply_changes_exactly_one_line_and_nothing_else():
    adapter = GoAdapter()
    site = next(s for s in adapter.find_candidates(SAMPLE, "sample.go") if s.original_token == ">")
    out = adapter.apply(SAMPLE, site)

    before, after = SAMPLE.splitlines(keepends=True), out.splitlines(keepends=True)
    assert len(before) == len(after)
    differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert differing == [site.lineno - 1]
    assert ">=" in after[site.lineno - 1]


@needs_go
def test_apply_preserves_comments_and_tabs():
    source = "package x\n\nfunc f(n int) bool {\n\tif n > 3 { // keep\tthis\n\t\treturn true\n\t}\n\treturn false\n}\n"
    adapter = GoAdapter()
    site = next(s for s in adapter.find_candidates(source, "x.go") if s.original_token == ">")
    assert "// keep\tthis" in adapter.apply(source, site)


@needs_go
def test_apply_refuses_when_the_source_has_drifted():
    adapter = GoAdapter()
    site = next(s for s in adapter.find_candidates(SAMPLE, "sample.go") if s.original_token == ">")
    drifted = SAMPLE.replace("if n > l.max", "if n < l.max")
    with pytest.raises(MutationError, match="original_token mismatch"):
        adapter.apply(drifted, site)


@needs_go
def test_drift_that_slips_past_the_token_check_is_caught_by_the_parser():
    """Belt and braces, and both are load-bearing.

    Widening `>` to `>=` leaves a `>` at the recorded offset, so the token
    check is satisfied and the splice produces `>==`. The syntax check is the
    only thing standing between that and a run.
    """
    adapter = GoAdapter()
    site = next(s for s in adapter.find_candidates(SAMPLE, "sample.go") if s.original_token == ">")
    drifted = SAMPLE.replace("if n > l.max", "if n >= l.max")
    with pytest.raises(MutationError, match="invalid syntax"):
        adapter.apply(drifted, site)


@needs_go
def test_apply_rejects_a_splice_that_does_not_parse():
    """The Go syntax check is what stops a bad span reaching the runner.

    Deleting the condition's operand leaves `if  {`, which the Go parser
    rejects -- exactly as ast.parse would on the Python side.
    """
    source = "package x\n\nfunc f(n int) bool {\n\tif n > 3 {\n\t\treturn true\n\t}\n\treturn false\n}\n"
    line = source.splitlines()[3].encode("utf-8")
    start = line.index(b"n > 3")
    bogus = MutationSite(
        path="x.go",
        lineno=4,
        col_start=start,
        col_end=start + len("n > 3"),
        operator_id="COMPARISON",
        original_token="n > 3",
        mutated_token="",
        enclosing_function_name="f",
    )
    with pytest.raises(MutationError, match="invalid syntax"):
        GoAdapter().apply(source, bogus)


# --------------------------------------------------------------------------
# discovery
# --------------------------------------------------------------------------


def test_discover_sources_skips_tests_fixtures_and_generated_code(tmp_path):
    (tmp_path / "request").mkdir()
    (tmp_path / "testdata").mkdir()
    (tmp_path / "parser.go").write_text("package x\n", encoding="utf-8")
    (tmp_path / "parser_test.go").write_text("package x\n", encoding="utf-8")
    (tmp_path / "request" / "r.go").write_text("package r\n", encoding="utf-8")
    (tmp_path / "testdata" / "f.go").write_text("package f\n", encoding="utf-8")
    (tmp_path / "zz_generated.go").write_text(
        "// Code generated by mockgen. DO NOT EDIT.\n\npackage x\n", encoding="utf-8"
    )

    found = {p.relative_to(tmp_path).as_posix() for p in GoAdapter().discover_sources(tmp_path)}
    assert found == {"parser.go", "request/r.go"}


# --------------------------------------------------------------------------
# failure extraction
# --------------------------------------------------------------------------

PANIC_OUTPUT = "\n".join(
    [
        "=== RUN   TestParser_Parse",
        "--- FAIL: TestParser_Parse (0.00s)",
        "panic: runtime error: index out of range [2] with length 0 [recovered, repanicked]",
        "",
        "goroutine 35 [running]:",
        "testing.tRunner.func1.2({0x7ff78fb1e0c0, 0x1a066059a018})",
        "\tC:/Program Files/Go/src/testing/testing.go:1974 +0x239",
        "panic({0x7ff78fb1e0c0?, 0x1a066059a018?})",
        "\tC:/Program Files/Go/src/runtime/panic.go:860 +0x13a",
        "github.com/golang-jwt/jwt/v5.(*Parser).ParseWithClaims(0x1a06605843c0, {0x7ff7?, 0x2a?})",
        "\tparser.go:80 +0x4a5",
        "github.com/golang-jwt/jwt/v5_test.TestParser_Parse.func1(0x1a06605d4400)",
        "\tparser_test.go:475 +0x1e8",
        "testing.tRunner(0x1a06605d4400, 0x1a06605823e0)",
        "\tC:/Program Files/Go/src/testing/testing.go:2036 +0xc3",
        "created by testing.(*T).Run in goroutine 34",
        "\tC:/Program Files/Go/src/testing/testing.go:2101 +0x4a9",
        "FAIL\tgithub.com/golang-jwt/jwt/v5\t4.809s",
        "",
    ]
)


def test_panic_frames_are_in_repo_only_and_outermost_first():
    """Both halves of this feed the difficulty score.

    Go prints a goroutine stack innermost first, and its deepest frames are
    the runtime's own unwinding. Left alone, a defect sitting exactly where
    the panic fired scores displacement 3 instead of 0.
    """
    frames, _ = parse_go_failure(PANIC_OUTPUT, ".:TestParser_Parse")
    assert [(f, n) for f, n, _ in frames] == [("parser_test.go", 475), ("parser.go", 80)]
    assert frames[-1][2] == "(*Parser).ParseWithClaims"


def test_displacement_is_zero_when_the_panic_fired_in_the_mutated_file():
    from bugforge.select import _displacement

    frames, _ = parse_go_failure(PANIC_OUTPUT, ".:TestParser_Parse")
    assert _displacement("parser.go", frames) == 0


def test_the_captured_text_keeps_the_runtime_frames():
    """Scoring ignores them; the learner's spine should still show them."""
    _, text = parse_go_failure(PANIC_OUTPUT, ".:TestParser_Parse")
    assert "panic: runtime error" in text
    assert "testing.go:2036" in text


def test_an_assertion_failure_yields_its_reported_locations():
    """The commoner shape: a t.Errorf report, with no stack at all."""
    output = "\n".join(
        [
            "=== RUN   TestVerifyAud",
            "    validator_test.go:123: Expected true, got false",
            "--- FAIL: TestVerifyAud (0.00s)",
            "FAIL\tgithub.com/golang-jwt/jwt/v5\t0.4s",
            "",
        ]
    )
    frames, text = parse_go_failure(output, ".:TestVerifyAud")
    assert [(f, n) for f, n, _ in frames] == [("validator_test.go", 123)]
    assert "Expected true, got false" in text


def test_relativize_strips_the_temp_tree_but_not_goroot():
    from pathlib import Path

    from bugforge.languages.go import relativize

    tree = Path("C:/tmp/bugforge-mutation-x/tree")
    text = "\tC:/tmp/bugforge-mutation-x/tree/parser.go:80 +0x1\n\tC:/Program Files/Go/src/testing/testing.go:1974 +0x2\n"
    out = relativize(text, tree)
    assert "parser.go:80" in out and "bugforge-mutation" not in out
    # GOROOT frames must survive, or the spine loses the shape of the stack.
    assert "C:/Program Files/Go/src/testing/testing.go:1974" in out
