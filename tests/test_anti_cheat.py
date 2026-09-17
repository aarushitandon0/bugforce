"""Unit tests for the patch hygiene checker.

This is the one piece of Phase 4 with an adversary. A false negative here
means a learner "passes" by deleting the assertion that caught the bug; a
false positive means an honest fix is rejected with no way to appeal. Both
fail silently in the sense that the verdict still looks like a verdict.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cloud.anti_cheat import (
    check_paths,
    check_tree_diff,
    compare,
    fingerprint,
    is_test_path,
    patch_target_paths,
)

CLEAN = """
import sys


def compute(a, b):
    if a < b:
        return a + b
    return a - b


def risky():
    try:
        return compute(1, 2)
    except ValueError:
        raise
"""


def _tree(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


# --------------------------------------------------------------------------
# diff parsing
# --------------------------------------------------------------------------

def test_patch_target_paths_strips_b_prefix():
    patch = "--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-x\n+y\n"
    assert patch_target_paths(patch) == ["pkg/mod.py"]


def test_patch_target_paths_handles_multiple_files_and_timestamps():
    patch = (
        "--- a/pkg/one.py\t2026-01-01 10:00:00\n"
        "+++ b/pkg/one.py\t2026-01-01 10:00:01\n"
        "@@ -1 +1 @@\n-x\n+y\n"
        "--- a/pkg/two.py\n+++ b/pkg/two.py\n@@ -1 +1 @@\n-x\n+y\n"
    )
    assert patch_target_paths(patch) == ["pkg/one.py", "pkg/two.py"]


def test_patch_target_paths_reports_deletions_by_source_path():
    patch = "--- a/tests/test_thing.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x\n"
    assert patch_target_paths(patch) == ["tests/test_thing.py"]


# --------------------------------------------------------------------------
# path rules
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "tests/test_retry.py",
        "tenacity/tests/test_thing.py",
        "test_top_level.py",
        "pkg/thing_test.py",
        "conftest.py",
        "pkg/conftest.py",
    ],
)
def test_test_files_are_rejected(path):
    assert is_test_path(path)
    result = check_paths([path])
    assert not result.ok
    assert result.reason == "anti_cheat"


def test_source_file_is_accepted():
    result = check_paths(["tenacity/retry.py"])
    assert result.ok
    assert result.touched_paths == ["tenacity/retry.py"]


def test_path_traversal_is_rejected():
    assert not check_paths(["../../etc/passwd"]).ok
    assert not check_paths(["/etc/passwd"]).ok


def test_non_python_file_is_rejected():
    # pyproject.toml / pytest.ini can disable the suite as effectively as
    # editing a test can.
    assert not check_paths(["pyproject.toml"]).ok
    assert not check_paths(["pytest.ini"]).ok


def test_empty_patch_is_rejected():
    assert not check_paths([]).ok


# --------------------------------------------------------------------------
# fingerprints
# --------------------------------------------------------------------------

def test_fingerprint_counts_clean_module():
    fp = fingerprint(CLEAN)
    assert fp.asserts == 0
    assert fp.swallowing_handlers == 0  # `except ValueError: raise` is not swallowing
    assert fp.skip_marks == 0
    assert fp.exit_calls == 0


def test_bare_except_pass_is_detected():
    fp = fingerprint("def f():\n    try:\n        g()\n    except:\n        pass\n")
    assert fp.swallowing_handlers == 1


def test_broad_except_pass_is_detected():
    fp = fingerprint("def f():\n    try:\n        g()\n    except Exception:\n        pass\n")
    assert fp.swallowing_handlers == 1


def test_narrow_except_with_handling_is_not_swallowing():
    source = "def f():\n    try:\n        g()\n    except ValueError:\n        return 1\n"
    assert fingerprint(source).swallowing_handlers == 0


def test_docstring_only_handler_counts_as_swallowing():
    # `except Exception: "explanation"` swallows exactly like `pass` does.
    source = 'def f():\n    try:\n        g()\n    except Exception:\n        "nope"\n'
    assert fingerprint(source).swallowing_handlers == 1


@pytest.mark.parametrize(
    "decorator",
    ["@pytest.mark.skip", "@pytest.mark.skipif(True)", "@pytest.mark.xfail", "@mark.xfail"],
)
def test_skip_markers_are_detected(decorator):
    source = f"import pytest\n{decorator}\ndef f():\n    pass\n"
    assert fingerprint(source).skip_marks == 1


def test_unrelated_decorator_is_not_a_skip_marker():
    source = "@functools.cache\ndef f():\n    pass\n"
    assert fingerprint(source).skip_marks == 0
    # a project's own decorator that merely contains "skip" in a longer name
    assert fingerprint("@skip_when_slow\ndef f():\n    pass\n").skip_marks == 0


@pytest.mark.parametrize("call", ["sys.exit(0)", "os._exit(1)", "pytest.exit('x')", "quit()"])
def test_exit_calls_are_detected(call):
    assert fingerprint(f"def f():\n    {call}\n").exit_calls == 1


def test_exit_inside_a_string_is_not_a_call():
    # the case a regex gets wrong
    source = 'def f():\n    return "call sys.exit(0) to quit"\n'
    assert fingerprint(source).exit_calls == 0


def test_commented_out_skip_marker_is_not_detected():
    source = "# @pytest.mark.skip\ndef f():\n    pass\n"
    assert fingerprint(source).skip_marks == 0


def test_assert_deletion_is_detected_by_comparison():
    before = fingerprint("def f():\n    assert 1\n    assert 2\n")
    after = fingerprint("def f():\n    assert 1\n")
    ok, detail = compare(before, after)
    assert not ok
    assert "assert" in detail


def test_adding_an_assert_is_allowed():
    before = fingerprint("def f():\n    pass\n")
    after = fingerprint("def f():\n    assert 1\n")
    assert compare(before, after)[0]


def test_preexisting_bare_except_is_not_held_against_the_patch():
    # The repo already swallows here; a patch that leaves it alone must pass.
    existing = "def f():\n    try:\n        g()\n    except Exception:\n        pass\n"
    before = fingerprint(existing)
    after = fingerprint(existing.replace("g()", "h()"))
    assert compare(before, after)[0]


# --------------------------------------------------------------------------
# tree-level check
# --------------------------------------------------------------------------

def test_honest_fix_passes(tmp_path):
    original = _tree(tmp_path, "orig", {"pkg/mod.py": CLEAN})
    patched = _tree(tmp_path, "work", {"pkg/mod.py": CLEAN.replace("a < b", "a <= b")})
    assert check_tree_diff(original, patched, ["pkg/mod.py"]).ok


def test_added_swallowing_handler_is_rejected(tmp_path):
    original = _tree(tmp_path, "orig", {"pkg/mod.py": CLEAN})
    cheat = CLEAN + "\n\ndef cheat():\n    try:\n        compute(1, 2)\n    except:\n        pass\n"
    patched = _tree(tmp_path, "work", {"pkg/mod.py": cheat})
    result = check_tree_diff(original, patched, ["pkg/mod.py"])
    assert not result.ok
    assert "except" in result.detail


def test_added_exit_call_is_rejected(tmp_path):
    original = _tree(tmp_path, "orig", {"pkg/mod.py": CLEAN})
    patched = _tree(tmp_path, "work", {"pkg/mod.py": CLEAN.replace("return a - b", "sys.exit(0)")})
    result = check_tree_diff(original, patched, ["pkg/mod.py"])
    assert not result.ok
    assert "exit" in result.detail


def test_unparseable_result_is_rejected(tmp_path):
    original = _tree(tmp_path, "orig", {"pkg/mod.py": CLEAN})
    patched = _tree(tmp_path, "work", {"pkg/mod.py": "def broken(:\n"})
    result = check_tree_diff(original, patched, ["pkg/mod.py"])
    assert not result.ok
    assert "unparseable" in result.detail


def test_deleted_file_is_rejected(tmp_path):
    original = _tree(tmp_path, "orig", {"pkg/mod.py": CLEAN})
    patched = _tree(tmp_path, "work", {"pkg/other.py": CLEAN})
    result = check_tree_diff(original, patched, ["pkg/mod.py"])
    assert not result.ok
    assert "deletes" in result.detail


def test_new_file_is_compared_against_an_empty_fingerprint(tmp_path):
    original = _tree(tmp_path, "orig", {"pkg/mod.py": CLEAN})
    patched = _tree(
        tmp_path,
        "work",
        {"pkg/mod.py": CLEAN, "pkg/new.py": "import pytest\n@pytest.mark.skip\ndef f():\n    pass\n"},
    )
    result = check_tree_diff(original, patched, ["pkg/new.py"])
    assert not result.ok
    assert "skip" in result.detail
