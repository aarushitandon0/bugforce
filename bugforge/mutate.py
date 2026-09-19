"""
Phase 2: AST mutation generation with surgical application.

find_candidates() uses the AST only to LOCATE tokens (line, byte offset).
apply() never calls ast.unparse -- it slices the exact byte span on one line
and splices the replacement back in, so every other byte of the file
(comments, formatting, unrelated lines) is untouched.

Column offsets throughout this module are UTF-8 BYTE offsets into a single
line, matching what Python's ast module reports. Mixing these up with
character offsets is the single easiest way to silently corrupt a file that
contains any non-ASCII character, so every offset computation here works on
`bytes`, never on `str` indices.
"""
from __future__ import annotations

import ast
from collections.abc import Callable
from dataclasses import dataclass

from bugforge.models import MutationSite

MAX_CANDIDATES_PER_FILE = 25

_SKIP_PATH_PARTS = {"tests", "test", "docs"}
_SKIP_FILENAMES = {"conftest.py", "setup.py"}
_SKIP_FUNCTION_NAMES = {"__repr__", "__str__"}

_COMPARISON_MUTATIONS = {
    ast.Lt: ("<", "<="),
    ast.LtE: ("<=", "<"),
    ast.Gt: (">", ">="),
    ast.GtE: (">=", ">"),
    ast.Eq: ("==", "!="),
}

_ARITHMETIC_MUTATIONS = {
    ast.Add: ("+", "-"),
    ast.Sub: ("-", "+"),
    ast.Mult: ("*", "//"),
}


class MutationError(RuntimeError):
    """Raised when a located candidate can't be safely applied."""


def is_mutable_source_path(path: str) -> bool:
    """File-level filter: skip tests, docs, and packaging scripts."""
    norm = path.replace("\\", "/")
    parts = set(norm.split("/"))
    filename = norm.rsplit("/", 1)[-1]
    if parts & _SKIP_PATH_PARTS:
        return False
    if filename in _SKIP_FILENAMES:
        return False
    if filename.startswith("test_"):
        return False
    return True


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _enclosing_function_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return None


def _enclosing_class_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, ast.ClassDef):
            return cur.name
    return None


def _type_checking_line_ranges(tree: ast.AST) -> list[tuple[int, int]]:
    ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_type_checking and node.body:
            ranges.append((node.body[0].lineno, node.body[-1].end_lineno))
    return ranges


def _in_type_checking(lineno: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= lineno <= end for start, end in ranges)


def _line_bytes(lines: list[str], lineno: int) -> bytes:
    return lines[lineno - 1].encode("utf-8")


@dataclass
class _Context:
    lines: list[str]
    parents: dict[ast.AST, ast.AST]
    type_checking_ranges: list[tuple[int, int]]
    path: str

    def enclosing_function_name(self, node: ast.AST) -> str | None:
        return _enclosing_function_name(node, self.parents)

    def enclosing_class_name(self, node: ast.AST) -> str | None:
        return _enclosing_class_name(node, self.parents)

    def skip(self, node: ast.AST) -> bool:
        if _in_type_checking(node.lineno, self.type_checking_ranges):
            return True
        fn = self.enclosing_function_name(node)
        return fn in _SKIP_FUNCTION_NAMES

    def make_site(
        self,
        node_for_skip_check: ast.AST,
        lineno: int,
        col_start: int,
        col_end: int,
        operator_id: str,
        original_token: str,
        mutated_token: str,
    ) -> MutationSite | None:
        if original_token == mutated_token:
            return None
        if self.skip(node_for_skip_check):
            return None
        return MutationSite(
            path=self.path,
            lineno=lineno,
            col_start=col_start,
            col_end=col_end,
            operator_id=operator_id,
            original_token=original_token,
            mutated_token=mutated_token,
            enclosing_function_name=self.enclosing_function_name(node_for_skip_check),
            enclosing_class_name=self.enclosing_class_name(node_for_skip_check),
        )


def _find_gap_token(
    ctx: _Context, lineno: int, gap_start: int, gap_end: int, token_bytes: bytes
) -> tuple[int, int] | None:
    """Finds `token_bytes` inside line[gap_start:gap_end] (both byte offsets).

    Returns absolute (col_start, col_end) or None if not found -- which
    happens when the operands span multiple lines and our same-line
    assumption doesn't hold, or when the token is otherwise not isolatable.
    """
    if gap_end < gap_start:
        return None
    line = _line_bytes(ctx.lines, lineno)
    gap = line[gap_start:gap_end]
    idx = gap.find(token_bytes)
    if idx == -1:
        return None
    return gap_start + idx, gap_start + idx + len(token_bytes)


def _find_comparison_sites(ctx: _Context, node: ast.Compare) -> list[MutationSite]:
    sites = []
    operands = [node.left, *node.comparators]
    for i, op in enumerate(node.ops):
        left, right = operands[i], operands[i + 1]
        mutation = _COMPARISON_MUTATIONS.get(type(op))
        if mutation is None:
            continue
        if left.end_lineno != node.lineno or right.lineno != node.lineno:
            continue  # multi-line comparison; skip rather than risk a bad splice
        original, mutated = mutation
        found = _find_gap_token(
            ctx, node.lineno, left.end_col_offset, right.col_offset, original.encode()
        )
        if found is None:
            continue
        col_start, col_end = found
        site = ctx.make_site(node, node.lineno, col_start, col_end, "COMPARISON", original, mutated)
        if site:
            sites.append(site)
    return sites


def _find_arithmetic_sites(ctx: _Context, node: ast.BinOp) -> list[MutationSite]:
    mutation = _ARITHMETIC_MUTATIONS.get(type(node.op))
    if mutation is None:
        return []
    left, right = node.left, node.right
    if left.end_lineno != node.lineno or right.lineno != node.lineno:
        return []
    original, mutated = mutation
    found = _find_gap_token(
        ctx, node.lineno, left.end_col_offset, right.col_offset, original.encode()
    )
    if found is None:
        return []
    col_start, col_end = found
    site = ctx.make_site(node, node.lineno, col_start, col_end, "ARITHMETIC", original, mutated)
    return [site] if site else []


def _find_boolean_sites(ctx: _Context, node: ast.BoolOp) -> list[MutationSite]:
    original = "and" if isinstance(node.op, ast.And) else "or"
    mutated = "or" if original == "and" else "and"
    sites = []
    for left, right in zip(node.values, node.values[1:]):
        if left.end_lineno != right.lineno:
            continue
        found = _find_gap_token(
            ctx, right.lineno, left.end_col_offset, right.col_offset, original.encode()
        )
        if found is None:
            continue
        col_start, col_end = found
        site = ctx.make_site(node, right.lineno, col_start, col_end, "BOOLEAN", original, mutated)
        if site:
            sites.append(site)
    return sites


def _find_boundary_sites(ctx: _Context, node: ast.Constant) -> list[MutationSite]:
    if type(node.value) is not int:  # excludes bool, which subclasses int
        return []
    parent = ctx.parents.get(node)
    in_slice = isinstance(parent, ast.Slice) and node in (parent.lower, parent.upper, parent.step)
    in_range_call = (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "range"
        and node in parent.args
    )
    in_comparison = isinstance(parent, ast.Compare) and node in (parent.left, *parent.comparators)
    if not (in_slice or in_range_call or in_comparison):
        return []
    if node.lineno != node.end_lineno:
        return []
    original = str(node.value)
    sites = []
    for delta in (1, -1):
        mutated = str(node.value + delta)
        site = ctx.make_site(
            node, node.lineno, node.col_offset, node.end_col_offset, "BOUNDARY", original, mutated
        )
        if site:
            sites.append(site)
    return sites


def _find_negation_sites(ctx: _Context, node: ast.If) -> list[MutationSite]:
    test = node.test
    if not (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)):
        return []
    operand = test.operand
    if test.lineno != operand.lineno:
        return []
    # span from the start of "not" through the start of the operand -- deleting
    # it turns "if not x:" into "if x:"
    original_bytes = _line_bytes(ctx.lines, test.lineno)[test.col_offset : operand.col_offset]
    if b"(" in original_bytes:
        # operand is parenthesized ("not (a or b)") -- the AST's operand
        # offset starts inside the parens, so deleting only this span would
        # strip the opening paren but leave its matching ")" dangling and
        # produce invalid syntax. Skip rather than chase the matching paren.
        return []
    site = ctx.make_site(
        node,
        test.lineno,
        test.col_offset,
        operand.col_offset,
        "NEGATION",
        original_bytes.decode("utf-8"),
        "",
    )
    return [site] if site else []


def _find_return_sites(ctx: _Context, node: ast.Return) -> list[MutationSite]:
    if node.value is None:
        return []
    if node.lineno != node.value.end_lineno:
        return []
    value = node.value
    if isinstance(value, ast.Constant) and value.value is True:
        original, mutated = "True", "False"
    else:
        original_bytes = _line_bytes(ctx.lines, node.lineno)[value.col_offset : value.end_col_offset]
        original = original_bytes.decode("utf-8")
        mutated = "None"
    site = ctx.make_site(
        node, node.lineno, value.col_offset, value.end_col_offset, "RETURN", original, mutated
    )
    return [site] if site else []


def _find_default_arg_sites(ctx: _Context, node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[MutationSite]:
    sites = []
    for default in (*node.args.defaults, *node.args.kw_defaults):
        if default is None or not isinstance(default, ast.Constant):
            continue
        if default.lineno != default.end_lineno:
            continue
        value = default.value
        if isinstance(value, bool):
            original, mutated = ("True", "False") if value else ("False", "True")
        elif type(value) is int:
            original, mutated = str(value), str(value + 1)
        else:
            continue
        site = ctx.make_site(
            default,
            default.lineno,
            default.col_offset,
            default.end_col_offset,
            "DEFAULT_ARG",
            original,
            mutated,
        )
        if site:
            sites.append(site)
    return sites


def find_candidates(source: str, path: str) -> list[MutationSite]:
    """Locates mutation sites in `source` using the AST for positions only.

    `path` is used for file-level skip rules (tests/, docs/, conftest.py,
    setup.py) and is stored on each MutationSite.
    """
    if not is_mutable_source_path(path):
        return []

    tree = ast.parse(source, filename=path)
    lines = source.splitlines(keepends=False)
    ctx = _Context(
        lines=lines,
        parents=_build_parent_map(tree),
        type_checking_ranges=_type_checking_line_ranges(tree),
        path=path,
    )

    candidates: list[MutationSite] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            candidates.extend(_find_comparison_sites(ctx, node))
        elif isinstance(node, ast.BinOp):
            candidates.extend(_find_arithmetic_sites(ctx, node))
        elif isinstance(node, ast.BoolOp):
            candidates.extend(_find_boolean_sites(ctx, node))
        elif isinstance(node, ast.Constant):
            candidates.extend(_find_boundary_sites(ctx, node))
        elif isinstance(node, ast.If):
            candidates.extend(_find_negation_sites(ctx, node))
        elif isinstance(node, ast.Return):
            candidates.extend(_find_return_sites(ctx, node))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            candidates.extend(_find_default_arg_sites(ctx, node))

    candidates.sort(key=lambda s: (s.lineno, s.col_start, s.operator_id, s.mutated_token))

    if len(candidates) > MAX_CANDIDATES_PER_FILE:
        stride = len(candidates) / MAX_CANDIDATES_PER_FILE
        candidates = [candidates[int(i * stride)] for i in range(MAX_CANDIDATES_PER_FILE)]

    return candidates


def _check_python_syntax(source: str, path: str) -> None:
    """Syntax validator for splice(): raises MutationError on invalid code."""
    try:
        ast.parse(source, filename=path)
    except SyntaxError as e:
        raise MutationError(f"mutation at {path} produced invalid syntax: {e}") from e


def splice(
    source: str,
    site: MutationSite,
    check_syntax: Callable[[str, str], None] = _check_python_syntax,
) -> str:
    """Applies one mutation via a surgical byte-level splice on a single line.

    Never touches ast.unparse -- every byte outside [col_start, col_end) on
    `site.lineno` is byte-identical to the input.

    This is the language-neutral half of Phase 2: nothing below reads Python
    syntax, it works on lines and UTF-8 byte offsets, which is all any
    language's positions reduce to. `check_syntax(source, path)` is the one
    language-specific step and must raise MutationError if the spliced result
    does not parse. Go passes its own (bugforge/languages/go.py) rather than
    reimplementing the offset arithmetic -- a second copy of this function is
    exactly the silent corruption the byte discipline here exists to prevent.
    """
    if "\n" in site.mutated_token or "\r" in site.mutated_token:
        raise MutationError("mutated_token must not span multiple lines")

    lines = source.splitlines(keepends=True)
    if not (1 <= site.lineno <= len(lines)):
        raise MutationError(f"lineno {site.lineno} out of range for source with {len(lines)} lines")

    raw_line = lines[site.lineno - 1]
    terminator = ""
    content = raw_line
    for ending in ("\r\n", "\n"):
        if raw_line.endswith(ending):
            terminator = ending
            content = raw_line[: -len(ending)]
            break

    content_bytes = content.encode("utf-8")
    if not (0 <= site.col_start <= site.col_end <= len(content_bytes)):
        raise MutationError(
            f"byte offsets [{site.col_start}:{site.col_end}] out of range for line "
            f"{site.lineno} ({len(content_bytes)} bytes)"
        )

    actual_span = content_bytes[site.col_start : site.col_end].decode("utf-8")
    if actual_span != site.original_token:
        raise MutationError(
            f"original_token mismatch at {site.path}:{site.lineno}: "
            f"expected {site.original_token!r}, found {actual_span!r} "
            "-- the source has drifted since the site was located"
        )

    new_bytes = (
        content_bytes[: site.col_start]
        + site.mutated_token.encode("utf-8")
        + content_bytes[site.col_end :]
    )
    new_line = new_bytes.decode("utf-8") + terminator
    lines[site.lineno - 1] = new_line
    mutated_source = "".join(lines)

    check_syntax(mutated_source, site.path)
    original_lines = source.splitlines(keepends=True)
    mutated_lines = mutated_source.splitlines(keepends=True)
    if len(original_lines) != len(mutated_lines):
        raise MutationError("mutation changed the number of lines in the file")
    changed = sum(1 for a, b in zip(original_lines, mutated_lines) if a != b)
    if changed != 1:
        raise MutationError(f"mutation touched {changed} lines, expected exactly 1")

    return mutated_source


def apply(source: str, site: MutationSite) -> str:
    """splice() with Python's syntax check -- the Phase 2 entry point."""
    return splice(source, site, _check_python_syntax)
