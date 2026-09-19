"""Go patch-hygiene rules.

The rule that earns its keep here is the exit one. `os.Exit(0)` spliced into
production code that runs during the suite ends the test binary with a
success status before a single result is printed, and `go test` prints "ok"
over the top of it -- so without this check a learner could turn any red
suite green without touching the defect.
"""
from __future__ import annotations

import shutil
import textwrap

import pytest

from cloud import anti_cheat

needs_go = pytest.mark.skipif(
    shutil.which("go") is None, reason="needs a Go toolchain to build the locator helper"
)

CLEAN = textwrap.dedent(
    """\
    package sample

    import "errors"

    func Check(n int) error {
    	if n > 3 {
    		return errors.New("too big")
    	}
    	return nil
    }
    """
)


def _write_pair(tmp_path, before: str, after: str, rel: str = "sample.go"):
    original = tmp_path / "original"
    patched = tmp_path / "patched"
    for root, text in ((original, before), (patched, after)):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return original, patched


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,ok",
    [
        ("parser.go", True),
        ("request/oauth2.go", True),
        ("parser_test.go", False),
        ("request/oauth2_test.go", False),
        ("testdata/fixture.go", False),
        ("test/helpers.go", False),
        ("parser.py", False),
        ("go.mod", False),
        ("../escape.go", False),
        ("/etc/passwd", False),
    ],
)
def test_go_path_rules(path, ok):
    assert anti_cheat.check_paths([path], "go").ok is ok


def test_a_python_patch_is_still_judged_by_pythons_rules():
    """The Go rules must not have loosened Python's."""
    assert anti_cheat.check_paths(["mod.py"], "python").ok is True
    assert anti_cheat.check_paths(["mod.go"], "python").ok is False
    assert anti_cheat.check_paths(["tests/test_mod.py"], "python").ok is False


def test_an_unknown_language_is_refused_rather_than_waved_through():
    result = anti_cheat.check_paths(["main.rs"], "rust")
    assert result.ok is False
    assert "rust" in result.detail


# --------------------------------------------------------------------------
# content
# --------------------------------------------------------------------------


@needs_go
def test_a_genuine_fix_is_accepted(tmp_path):
    fixed = CLEAN.replace("n > 3", "n >= 3")
    original, patched = _write_pair(tmp_path, CLEAN, fixed)
    result = anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go")
    assert result.ok is True, result.detail


@needs_go
def test_adding_os_exit_is_rejected(tmp_path):
    cheat = CLEAN.replace(
        'import "errors"',
        'import (\n\t"errors"\n\t"os"\n)',
    ).replace("\tif n > 3 {", "\tos.Exit(0)\n\tif n > 3 {")
    original, patched = _write_pair(tmp_path, CLEAN, cheat)
    result = anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go")
    assert result.ok is False
    assert "process-exit" in result.detail


@needs_go
def test_an_exit_call_the_repo_already_had_is_not_held_against_the_patch(tmp_path):
    """The comparison is before-vs-after, never an absolute count.

    Plenty of real Go code calls log.Fatal in a main(). A patch that leaves
    it exactly where it found it has cheated at nothing.
    """
    before = CLEAN.replace(
        'import "errors"',
        'import (\n\t"errors"\n\t"log"\n)',
    ).replace("\treturn nil", '\tif n < 0 {\n\t\tlog.Fatal("negative")\n\t}\n\treturn nil')
    after = before.replace("n > 3", "n >= 3")
    original, patched = _write_pair(tmp_path, before, after)
    assert anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go").ok is True


@needs_go
def test_adding_a_build_constraint_is_rejected(tmp_path):
    cheat = "//go:build ignore\n\n" + CLEAN
    original, patched = _write_pair(tmp_path, CLEAN, cheat)
    result = anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go")
    assert result.ok is False
    assert "go:build" in result.detail


@needs_go
def test_a_patch_that_does_not_compile_is_rejected_not_crashed_on(tmp_path):
    original, patched = _write_pair(tmp_path, CLEAN, "package sample\n\nfunc Check(")
    result = anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go")
    assert result.ok is False
    assert "unparseable" in result.detail


@needs_go
def test_deleting_the_file_is_rejected(tmp_path):
    original, patched = _write_pair(tmp_path, CLEAN, CLEAN)
    (patched / "sample.go").unlink()
    result = anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go")
    assert result.ok is False
    assert "deletes" in result.detail


@needs_go
def test_a_string_mentioning_os_exit_is_not_a_call(tmp_path):
    """The reason this is AST-based and not a regex."""
    after = CLEAN.replace('errors.New("too big")', 'errors.New("do not call os.Exit(0) here")')
    original, patched = _write_pair(tmp_path, CLEAN, after)
    assert anti_cheat.check_tree_diff(original, patched, ["sample.go"], "go").ok is True
