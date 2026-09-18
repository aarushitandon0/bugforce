"""Phase 5: the system's only model call -- a bug-ticket title and two sentences.

Everything else in BugForge -- which mutation is made, which one becomes a
challenge, how hard it is, and whether a fix is correct -- is AST work and
test execution. This module writes prose and nothing else.

Order of operations in `describe()`:

1. Build the fallback first. It is a fixed template over facts the pipeline
   already computed deterministically, so it always exists.
2. If BUGFORGE_DISABLE_BEDROCK is set, return the fallback. No client is
   constructed and `anthropic` is never imported.
3. Otherwise ask the model for strict JSON and run the hard post-check in
   code. On rejection retry exactly once, then fall back. A failed API call
   (no credentials, throttled, SDK missing) falls back immediately -- the SDK
   has already done its own transport retries by then.

The post-check rejects any output that names a file path, a line number, or
any identifier that appears in the mutated line's enclosing scope, the module
path, or the failing test's id. Match is whole-word and case-insensitive.
"""
from __future__ import annotations

import ast
import builtins
import json
import keyword
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from bugforge.models import MutationSite

log = logging.getLogger(__name__)

DISABLE_ENV = "BUGFORGE_DISABLE_BEDROCK"
DEFAULT_MODEL_ID = "anthropic.claude-opus-5"
MAX_DOCSTRING_CHARS = 1500
MAX_VALUE_CHARS = 80
# Identifiers shorter than this ("a", "i", "fn") collide with ordinary English
# and are not meaningful leaks.
MIN_IDENTIFIER_LEN = 3

OPERATOR_LABELS = {
    "COMPARISON": "Comparison",
    "ARITHMETIC": "Arithmetic",
    "BOOLEAN": "Boolean",
    "BOUNDARY": "Boundary",
    "NEGATION": "Negation",
    "RETURN": "Return",
    "DEFAULT_ARG": "Default",
}

_PROMPT = """You write the title and description for a bug ticket.

A defect makes one automated check fail in an open-source Python library. \
Here is everything known about the failure:

What the library module does:
{docstring}

Failing check: {test_name}
Expected: {expected}
Actual: {actual}
Exception type: {exception_type}

Write the ticket as a user or operator of the library would report the \
symptom they observe. Do not name any file, module, function, class, method, \
variable, test, or line number, and do not guess at the cause.

Respond with a single JSON object and nothing else -- no prose, no markdown \
fences:
{{"title": "...", "description": "..."}}

title: 2 or 3 words, evocative, like a bug ticket (for example "Retry Storm" \
or "Ghost Session").
description: exactly two sentences describing the symptom. For example: \
"A failed request keeps retrying instead of giving up after three attempts. \
Throughput collapses under load.\""""

_RETRY_SUFFIX = "\n\nYour previous answer was rejected ({reason}). Follow every rule above."


class Rejected(ValueError):
    """Model output failed the hard post-check."""


@dataclass
class DescribeInput:
    """Everything describe() needs. All of it is computed deterministically."""

    test_name: str  # short name, e.g. "test_stop_after_attempt"
    expected: str | None
    actual: str | None
    exception_type: str | None
    module_docstring: str
    operator_id: str
    module_path: str  # fallback title + post-check only; never sent to the model
    forbidden_identifiers: set[str] = field(default_factory=set)
    # Fallback title only, and never sent to the model: the module basename
    # alone collides constantly (16 of 20 tenacity challenges are RETURN
    # mutations, so "Return in retry" appeared six times).
    enclosing_function: str | None = None
    enclosing_class: str | None = None


@dataclass
class Description:
    title: str
    description: str
    source: str  # "bedrock" or "template"
    rejections: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# deterministic inputs
# ---------------------------------------------------------------------------

_E_LINE_RE = re.compile(r"^E\s+(.*)$", re.MULTILINE)
_PYTEST_EQ_RE = re.compile(r"^(?:AssertionError: )?assert (.+?) == (.+)$")
_UNITTEST_NE_RE = re.compile(r"^AssertionError: (?:[A-Z][\w ]* differ: )?(.+?) != (.+)$")
_UNITTEST_NOT_BOOL_RE = re.compile(r"^AssertionError: (.+?) is not (true|false)$")
_UNITTEST_INSTANCE_RE = re.compile(r"^AssertionError: (.+?) is not an instance of <class '(?:[\w.]+\.)?(\w+)'>$")
_DID_NOT_RAISE_RE = re.compile(r"^Failed: DID NOT RAISE (?:<class '(?:[\w.]+\.)?(\w+)'>|(\w+))")
# A bare or dotted exception name, alone or followed by ": message". The last
# dotted component must be capitalised: `Exception`, `pkg.mod.<locals>.CustomError`.
_EXCEPTION_RE = re.compile(r"^((?:[\w<>]+\.)*[A-Z]\w*)(?::|$)")
_NOT_EXCEPTIONS = {"True", "False", "None"}


def _clip(value: str) -> str:
    value = value.strip()
    return value if len(value) <= MAX_VALUE_CHARS else value[: MAX_VALUE_CHARS - 3] + "..."


def parse_failure(traceback_text: str) -> tuple[str | None, str | None, str | None]:
    """Returns (expected, actual, exception_type) from pytest --tb=long output.

    Convention for the two sides of an equality: `assert actual == expected`
    (pytest) and `assertEqual(actual, expected)` (unittest). A test written
    the other way round gets the two labels swapped; nothing else breaks.
    """
    e_lines = [line.strip() for line in _E_LINE_RE.findall(traceback_text or "")]
    expected = actual = exception_type = None

    for line in e_lines:
        if match := _PYTEST_EQ_RE.match(line) or _UNITTEST_NE_RE.match(line):
            actual, expected = _clip(match.group(1)), _clip(match.group(2))
        elif match := _UNITTEST_NOT_BOOL_RE.match(line):
            actual, expected = _clip(match.group(1)), match.group(2).capitalize()
        elif match := _UNITTEST_INSTANCE_RE.match(line):
            actual, expected = _clip(match.group(1)), f"an instance of {match.group(2)}"
        elif match := _DID_NOT_RAISE_RE.match(line):
            actual, expected = "no exception", f"{match.group(1) or match.group(2)} to be raised"
        else:
            continue
        break

    for line in e_lines:
        match = _EXCEPTION_RE.match(line)
        if match and match.group(1) not in _NOT_EXCEPTIONS:
            # last one wins: the innermost raise of a chained traceback
            exception_type = match.group(1).rsplit(".", 1)[-1]
    if exception_type is None and any(line.startswith("assert ") for line in e_lines):
        exception_type = "AssertionError"

    return expected, actual, exception_type


def short_test_name(test_id: str) -> str:
    return test_id.split("::")[-1]


def module_basename(path: str) -> str:
    """`tenacity/retry.py` -> `retry`; a package `__init__.py` -> its package name."""
    p = PurePosixPath(path.replace("\\", "/"))
    return p.parent.name if p.stem == "__init__" and p.parent.name else p.stem


_NOT_A_LEAK = set(keyword.kwlist) | set(dir(builtins)) | {"self", "cls"}


def _usable(name: str | None) -> bool:
    return bool(name) and len(name) >= MIN_IDENTIFIER_LEN and name not in _NOT_A_LEAK


def _names_in(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, ast.Attribute):
            names.add(child.attr)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
        elif isinstance(child, ast.keyword) and child.arg:
            names.add(child.arg)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.alias):
            names.add((child.asname or child.name).split(".")[0])
    return names


def scope_identifiers(source: str, lineno: int) -> set[str]:
    """Every identifier in the innermost def/class enclosing `lineno`, plus the
    names of all defs/classes that enclose it. For a module-level line, the
    top-level statement containing it stands in for the scope."""
    tree = ast.parse(source)
    enclosing: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.lineno <= lineno <= (node.end_lineno or node.lineno):
                enclosing.append(node)

    if enclosing:
        innermost = max(enclosing, key=lambda n: n.lineno)
        names = _names_in(innermost) | {n.name for n in enclosing}
    else:
        names = set()
        for stmt in tree.body:
            if stmt.lineno <= lineno <= (stmt.end_lineno or stmt.lineno):
                names = _names_in(stmt)
    return {n for n in names if _usable(n)}


def build_input(
    site: MutationSite, source: str, failing_test: str, traceback_text: str
) -> DescribeInput:
    expected, actual, exception_type = parse_failure(traceback_text)

    forbidden = scope_identifiers(source, site.lineno)
    forbidden.add(site.enclosing_function_name or "")
    path = PurePosixPath(site.path.replace("\\", "/"))
    forbidden |= {path.stem, *path.parts[:-1]}
    test_path, *test_parts = failing_test.split("::")
    forbidden |= set(test_parts) | {PurePosixPath(test_path).stem}

    return DescribeInput(
        test_name=short_test_name(failing_test),
        expected=expected,
        actual=actual,
        exception_type=exception_type,
        module_docstring=ast.get_docstring(ast.parse(source)) or "",
        operator_id=site.operator_id,
        module_path=site.path,
        forbidden_identifiers={n for n in forbidden if _usable(n)},
        enclosing_function=site.enclosing_function_name,
        enclosing_class=site.enclosing_class_name,
    )


# ---------------------------------------------------------------------------
# deterministic fallback
# ---------------------------------------------------------------------------

def title_subject(inp: DescribeInput) -> str:
    """What the fallback title says the defect is "in".

    The enclosing function, except that a dunder or a private helper names
    nothing a learner would recognise -- "Return in __call__" was 7 of the 20
    tenacity titles -- so for those the enclosing class wins. Module-level
    mutations have neither and fall back to the module basename.
    """
    fn = inp.enclosing_function
    if fn and inp.enclosing_class and fn.startswith("_"):
        return inp.enclosing_class
    return fn or inp.enclosing_class or module_basename(inp.module_path)


def fallback_description(inp: DescribeInput) -> Description:
    """The template. Works with Bedrock switched off, and is the spec'd shape:
    title "{operator_label} in {enclosing_function or module_basename}",
    description "{test_name} expected {expected}, got {actual}."
    A failure with no equality assertion has no expected/actual to report, so
    it names the exception type instead."""
    label = OPERATOR_LABELS.get(inp.operator_id, inp.operator_id.replace("_", " ").title())
    where = title_subject(inp)
    title = f"{label} in {where}"
    if inp.expected is not None and inp.actual is not None:
        description = f"{inp.test_name} expected {inp.expected}, got {inp.actual}."
    elif inp.exception_type:
        description = f"{inp.test_name} raised {inp.exception_type}."
    else:
        description = f"{inp.test_name} failed."
    return Description(title=title, description=description, source="template")


def disambiguate_titles(titles: list[str], linenos: list[int]) -> list[str]:
    """Appends ":<lineno>" to every title that is not unique in the batch.

    Two mutations of the same operator inside one function still collide
    (a function with two returns), and a grid of identical card titles
    reads as a broken build. Line number is the cheapest tiebreak that
    stays deterministic; it leaks no more than the title already does.
    """
    counts: dict[str, int] = {}
    for title in titles:
        counts[title] = counts.get(title, 0) + 1
    return [
        f"{title}:{lineno}" if counts[title] > 1 else title
        for title, lineno in zip(titles, linenos)
    ]


# ---------------------------------------------------------------------------
# hard post-check
# ---------------------------------------------------------------------------

_FILE_PATH_RE = re.compile(r"[\w-]+\.(?:py|pyc|cfg|toml|ini|txt)\b|\w[/\\]\w", re.IGNORECASE)
_LINE_NUMBER_RE = re.compile(r"\blines?\s*#?\d+|\bL\d+\b|:\d+\b", re.IGNORECASE)
_CODE_SHAPE_RE = re.compile(r"`|\w\(\)|\b[A-Za-z]+_\w+|\b[A-Za-z_]\w*\.[A-Za-z_]\w*\(")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def check_output(text: str, inp: DescribeInput) -> tuple[str, str]:
    """Returns (title, description) or raises Rejected with the reason."""
    try:
        payload = json.loads(text.strip())
    except (json.JSONDecodeError, AttributeError) as e:
        raise Rejected(f"not valid JSON: {e}") from e
    if not isinstance(payload, dict) or set(payload) != {"title", "description"}:
        raise Rejected("JSON must have exactly the keys title and description")
    title, description = payload["title"], payload["description"]
    if not isinstance(title, str) or not isinstance(description, str):
        raise Rejected("title and description must be strings")
    title, description = title.strip(), description.strip()

    if not 2 <= len(title.split()) <= 3:
        raise Rejected(f"title must be 2-3 words, got {len(title.split())}")
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(description) if s]
    if len(sentences) != 2 or description[-1] not in ".!?":
        raise Rejected(f"description must be exactly 2 sentences, got {len(sentences)}")

    combined = f"{title}\n{description}"
    if _FILE_PATH_RE.search(combined):
        raise Rejected("names a file path")
    if _LINE_NUMBER_RE.search(combined):
        raise Rejected("names a line number")
    if _CODE_SHAPE_RE.search(combined):
        raise Rejected("contains a code-shaped identifier")
    for name in sorted(inp.forbidden_identifiers):
        if re.search(rf"\b{re.escape(name)}\b", combined, re.IGNORECASE):
            raise Rejected(f"names identifier {name!r} from the mutation scope")

    return title, description


# ---------------------------------------------------------------------------
# Bedrock
# ---------------------------------------------------------------------------

def bedrock_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _client():
    # Imported lazily: with Bedrock disabled, `anthropic` is never imported,
    # so a missing or broken SDK cannot affect the pipeline.
    from anthropic import AnthropicBedrockMantle

    return AnthropicBedrockMantle(aws_region=os.environ.get("AWS_REGION", "us-east-1"))


def build_prompt(inp: DescribeInput) -> str:
    return _PROMPT.format(
        docstring=(inp.module_docstring or "(no module docstring)")[:MAX_DOCSTRING_CHARS],
        test_name=inp.test_name,
        expected=inp.expected if inp.expected is not None else "(not an equality check)",
        actual=inp.actual if inp.actual is not None else "(not an equality check)",
        exception_type=inp.exception_type or "(none)",
    )


def _call_model(prompt: str) -> str:
    response = _client().messages.create(
        model=os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    if response.stop_reason == "refusal":
        raise Rejected("model refused")
    return "".join(b.text for b in response.content if b.type == "text")


def describe(inp: DescribeInput) -> Description:
    """Model-written copy that passed the post-check, or the fallback. Never raises."""
    fallback = fallback_description(inp)
    if bedrock_disabled():
        return fallback

    prompt = build_prompt(inp)
    rejections: list[str] = []
    for _attempt in range(2):  # the first try, then exactly one retry
        try:
            text = _call_model(prompt)
            title, description = check_output(text, inp)
            return Description(title, description, source="bedrock", rejections=rejections)
        except Rejected as e:
            rejections.append(str(e))
            prompt = build_prompt(inp) + _RETRY_SUFFIX.format(reason=e)
        except Exception as e:  # noqa: BLE001 -- prose is never worth failing a run over
            log.warning("bedrock call failed (%s); using fallback", e)
            rejections.append(f"model call failed: {type(e).__name__}")
            break

    log.info("describe fell back to template after: %s", rejections)
    return Description(fallback.title, fallback.description, source="template", rejections=rejections)
