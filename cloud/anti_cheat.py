"""Patch hygiene, checked with the AST rather than regexes.

A learner's patch is rejected before it is ever graded if it:
  * touches a test file (or anything that isn't a non-test .py file)
  * deletes an assert statement
  * adds a swallowing `except: pass`
  * adds an @pytest.mark.skip / skipif / xfail decorator
  * adds a call to sys.exit / os._exit / pytest.exit / quit

Every content rule is a BEFORE vs AFTER comparison on the same file, never an
absolute count: repos legitimately contain bare excepts and xfail markers
already, and a patch that leaves them alone must not be punished for them.

A regex would be both too eager (the string "sys.exit" in a docstring) and too
easy to slip past ("except  :" / `\\\n` continuations / `exec`), which is why
the rules below only ever look at parsed nodes.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

REJECT_REASON = "anti_cheat"

# Extension a patch may touch, per language. Anything else is rejected at the
# path stage, before the patch is applied to a tree.
_SOURCE_SUFFIX = {"python": ".py", "go": ".go"}

_EXIT_CALLS = {"sys.exit", "os._exit", "pytest.exit", "exit", "quit", "os.abort"}
_SKIP_MARKS = {"skip", "skipif", "xfail"}

# "+++ b/path/to/file.py" or "+++ path/to/file.py", with an optional
# trailing tab-separated timestamp that some diff tools emit.
_TARGET_RE = re.compile(r"^\+\+\+ (?:b/)?([^\t\n]+)", re.MULTILINE)
_SOURCE_RE = re.compile(r"^--- (?:a/)?([^\t\n]+)", re.MULTILINE)


@dataclass
class HygieneResult:
    ok: bool
    reason: str = ""
    detail: str = ""
    touched_paths: list[str] = field(default_factory=list)


def _normalize(path: str) -> str:
    return path.replace("\\", "/").strip()


def patch_target_paths(patch_text: str) -> list[str]:
    """Every path the unified diff writes to, in file order.

    /dev/null targets (pure deletions) are reported as the source path so the
    path rules below still get a look at them.
    """
    paths: list[str] = []
    sources = _SOURCE_RE.findall(patch_text)
    for i, target in enumerate(_TARGET_RE.findall(patch_text)):
        target = _normalize(target)
        if target == "/dev/null":
            target = _normalize(sources[i]) if i < len(sources) else "/dev/null"
        if target not in paths:
            paths.append(target)
    return paths


def is_test_path(path: str) -> bool:
    norm = _normalize(path)
    parts = PurePosixPath(norm).parts
    name = PurePosixPath(norm).name
    if {"tests", "test", "testing"} & set(parts):
        return True
    # `_test.go` is Go's entire test convention, and `testdata` is a directory
    # the Go toolchain refuses to build -- a patch "fixing" something in there
    # has not fixed anything.
    if "testdata" in parts or name.endswith("_test.go"):
        return True
    return name.startswith("test_") or name.endswith("_test.py") or name == "conftest.py"


def check_paths(paths: list[str], language: str = "python") -> HygieneResult:
    """Path-level rules, applied before the patch is allowed anywhere near the tree."""
    if not paths:
        return HygieneResult(False, REJECT_REASON, "patch does not target any file")
    suffix = _SOURCE_SUFFIX.get(language.lower())
    if suffix is None:
        return HygieneResult(
            False, REJECT_REASON, f"no patch rules are defined for language {language!r}"
        )
    for path in paths:
        norm = _normalize(path)
        if norm.startswith("/") or norm.startswith("\\") or ".." in PurePosixPath(norm).parts:
            return HygieneResult(False, REJECT_REASON, f"patch targets a path outside the tree: {path}")
        if is_test_path(norm):
            return HygieneResult(False, REJECT_REASON, f"patch modifies a test file: {path}")
        if not norm.endswith(suffix):
            return HygieneResult(
                False,
                REJECT_REASON,
                f"patch modifies a file that is not {language} source: {path}",
            )
    return HygieneResult(True, touched_paths=[_normalize(p) for p in paths])


# ---------------------------------------------------------------------------
# AST fingerprint
# ---------------------------------------------------------------------------

def _dotted_name(node: ast.AST) -> str:
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        return ""
    return ".".join(reversed(parts))


def _is_skip_decorator(node: ast.AST) -> bool:
    target = node.func if isinstance(node, ast.Call) else node
    dotted = _dotted_name(target)
    if not dotted:
        return False
    segments = dotted.split(".")
    if segments[-1] not in _SKIP_MARKS:
        return False
    # @pytest.mark.skip, @mark.xfail, and a `from pytest.mark import skipif`
    # style bare @skipif all count; a project's own @skip_when_slow does not,
    # because its last segment isn't one of the three marks.
    return "mark" in segments or "pytest" in segments or len(segments) == 1


def _is_swallowing_handler(handler: ast.ExceptHandler) -> bool:
    """A handler that catches broadly and does nothing at all."""
    caught_broadly = handler.type is None or _dotted_name(handler.type) in {
        "Exception",
        "BaseException",
    }
    if not caught_broadly:
        return False
    return all(isinstance(stmt, ast.Pass) for stmt in handler.body) or all(
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
        for stmt in handler.body
    )


@dataclass
class Fingerprint:
    asserts: int = 0
    swallowing_handlers: int = 0
    skip_marks: int = 0
    exit_calls: int = 0


def fingerprint(source: str, filename: str = "<patch>") -> Fingerprint:
    """Counts the four things the hygiene rules care about. Raises SyntaxError
    if the source doesn't parse."""
    tree = ast.parse(source, filename=filename)
    fp = Fingerprint()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            fp.asserts += 1
        elif isinstance(node, ast.ExceptHandler):
            if _is_swallowing_handler(node):
                fp.swallowing_handlers += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            fp.skip_marks += sum(1 for d in node.decorator_list if _is_skip_decorator(d))
        elif isinstance(node, ast.Call):
            if _dotted_name(node.func) in _EXIT_CALLS:
                fp.exit_calls += 1
    return fp


def compare(before: Fingerprint, after: Fingerprint) -> tuple[bool, str]:
    if after.asserts < before.asserts:
        return False, f"patch deletes {before.asserts - after.asserts} assert statement(s)"
    if after.swallowing_handlers > before.swallowing_handlers:
        return False, "patch adds a bare `except: pass` that swallows the failure"
    if after.skip_marks > before.skip_marks:
        return False, "patch adds a pytest skip/xfail marker"
    if after.exit_calls > before.exit_calls:
        return False, "patch adds a process-exit call (sys.exit / os._exit / pytest.exit)"
    return True, ""


@dataclass
class GoFingerprint:
    """Go's counterpart to Fingerprint. See the module docstring for why it is
    shorter -- it is not an unfinished version of the Python one."""

    exit_calls: int = 0
    skip_calls: int = 0
    build_ignores: int = 0


def go_fingerprint(source: str, filename: str = "<patch>") -> GoFingerprint:
    """Counts what the Go hygiene rules care about, via go/ast.

    Raises SyntaxError if the source does not parse, so the caller's existing
    "patch leaves the file unparseable" branch works unchanged across both
    languages.
    """
    # Imported here rather than at module scope: fn_grade imports anti_cheat in
    # every image, and the Python images have no Go toolchain to build the
    # helper with. A Python repo must never pay for Go being supported.
    from bugforge.languages.go import _call_locator

    result = _call_locator("fingerprint", filename, source)
    if result.get("error"):
        raise SyntaxError(result["error"])
    raw = result.get("fingerprint") or {}
    return GoFingerprint(
        exit_calls=raw.get("exit_calls", 0),
        skip_calls=raw.get("skip_calls", 0),
        build_ignores=raw.get("build_ignores", 0),
    )


def compare_go(before: GoFingerprint, after: GoFingerprint) -> tuple[bool, str]:
    if after.exit_calls > before.exit_calls:
        return False, "patch adds a process-exit call (os.Exit / syscall.Exit / log.Fatal)"
    if after.skip_calls > before.skip_calls:
        return False, "patch adds a test-skip call"
    if after.build_ignores > before.build_ignores:
        return False, "patch adds a //go:build constraint that could exclude the file"
    return True, ""


# Per language: how to fingerprint a file, how to compare two fingerprints,
# and what an absent "before" file counts as.
_RULES = {
    "python": (fingerprint, compare, Fingerprint),
    "go": (go_fingerprint, compare_go, GoFingerprint),
}


def check_tree_diff(
    original_dir: Path, patched_dir: Path, touched_paths: list[str], language: str = "python"
) -> HygieneResult:
    """Compares each touched file's before/after AST fingerprint.

    A file the patch creates has no "before", so it is compared against an
    empty fingerprint -- which still catches a brand-new module full of
    skip markers.
    """
    rules = _RULES.get(language.lower())
    if rules is None:
        return HygieneResult(
            False, REJECT_REASON, f"no patch rules are defined for language {language!r}"
        )
    take_fingerprint, compare_fingerprints, empty = rules

    for rel in touched_paths:
        after_path = patched_dir / rel
        if not after_path.exists():
            return HygieneResult(False, REJECT_REASON, f"patch deletes {rel}")
        try:
            after_fp = take_fingerprint(after_path.read_text(encoding="utf-8"), rel)
        except SyntaxError as e:
            return HygieneResult(False, REJECT_REASON, f"patch leaves {rel} unparseable: {e}")

        before_path = original_dir / rel
        if before_path.exists():
            before_fp = take_fingerprint(before_path.read_text(encoding="utf-8"), rel)
        else:
            before_fp = empty()

        ok, detail = compare_fingerprints(before_fp, after_fp)
        if not ok:
            return HygieneResult(False, REJECT_REASON, f"{rel}: {detail}")

    return HygieneResult(True, touched_paths=touched_paths)
