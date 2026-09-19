"""
Go: the second LanguageAdapter.

Where the Python adapter forwards everything to phases 1-3, this one has to
supply the parts Go's toolchain spells differently:

* **Locating tokens** is delegated to `golocate/`, a small Go program built
  from `go/ast`. Go's AST records the exact position of every operator
  (`BinaryExpr.OpPos`), which Python's does not, so the locator is actually
  more precise than mutate.py's gap-search. Splicing still happens in Python
  via `mutate.splice` -- this module supplies only the syntax check.

* **The coverage map** is the expensive part. Python hands us per-test
  contexts for free; Go's `-coverprofile` is a whole-run profile with no
  per-test attribution at all. So we run the suite once per test, each with
  its own profile, and union the results. That is O(tests) suite runs -- ~3s
  per test on the vetted repo -- which is only tolerable because the result
  is cached per `(repo, commit_sha)` exactly like the Python baseline, so it
  happens once per commit and never again.

* **A compile gate.** Go rejects at build time what Python surfaces as a test
  failure, so a mutation that does not type-check is a build error rather
  than a red test. `run_tests` reports that as `collection_error`, which is
  the same bucket Phase 3 already drops Python import errors into.

Test ids are `"<pkg dir>:<TestName>"`, with "." for the module root --
`".:TestParser"`, `"request:TestParseFromRequest"`. The dir half is what
`run_tests` needs to target a package and the name half goes straight into a
`-run` anchor, so the id round-trips without a lookup table.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path

from bugforge import baseline as _baseline
from bugforge import mutate as _mutate
from bugforge import runner as _runner
from bugforge.models import Baseline, LineToTests, MutationSite, RunnerConfig

SOURCE_SUFFIX = ".go"

# Same spirit as the Python adapter's list: build output, vendored code and
# VCS metadata. `testdata` is Go-specific and load-bearing -- the toolchain
# ignores it when building, so anything in there is fixture data that no test
# executes, and mutating it would produce a challenge nothing can catch.
_SKIP_DIRS = {
    ".git",
    "vendor",
    "testdata",
    "docs",
    "node_modules",
    ".idea",
    # Go's test convention is the `_test.go` suffix, not a directory, so a
    # package actually named `test` is test *infrastructure* that production
    # code never imports -- golang-jwt/jwt's `test/helpers.go` is exactly
    # that. Mutating it breaks the tests without there being a defect for a
    # learner to find. Same two names the Python adapter skips.
    "test",
    "tests",
}

# `go test` writes these; a stale one in a materialized tree confuses nothing
# but wastes copy time.
_COPY_IGNORE = (".git", "*.test", "*.exe", "*.out", ".coverage")

_GENERATED_RE = re.compile(r"^// Code generated .* DO NOT EDIT\.$", re.MULTILINE)

# "import/path/file.go:12.34,15.2 3 1" -- start line.col, end line.col, number
# of statements in the block, execution count.
_PROFILE_RE = re.compile(r"^(.+):(\d+)\.\d+,(\d+)\.\d+ \d+ (\d+)$")

_LIST_TEST_RE = re.compile(r"^(Test\w*)$", re.MULTILINE)
# Deliberately NOT tolerant of leading whitespace. `go test -v` indents
# subtest results under their parent, and a table-driven test can have fifty
# of them -- counting those would report 70 results for the 8 tests we asked
# for and hand the scorer a "fraction of the suite that went red" computed
# against a different denominator than the baseline's test count. Top-level
# results only, which is what `total_tests` counts too.
_RESULT_RE = re.compile(r"^--- (PASS|FAIL|SKIP): (\S+)", re.MULTILINE)
# A package that failed to build, rather than a test that failed.
_BUILD_FAIL_RE = re.compile(r"\[build failed\]|^# \S", re.MULTILINE)


class GoToolchainError(RuntimeError):
    """Raised when the Go toolchain or the locator helper is unusable."""


# --------------------------------------------------------------------------
# the locator helper
# --------------------------------------------------------------------------

_LOCATOR_SRC = Path(__file__).resolve().parent / "golocate"
_locator_bin: str | None = None


def _go_exe(runner: RunnerConfig) -> str:
    go = runner.go or "go"
    found = shutil.which(go)
    if found is None:
        raise GoToolchainError(
            f"{go!r} is not on PATH. The Go adapter needs a Go toolchain both to "
            "locate tokens and to run tests; the Lambda image installs one."
        )
    return found


def locator_binary(go: str = "go") -> str:
    """Path to the built `golocate` helper, building it on first use.

    The Lambda image builds it once at image build time and points
    BUGFORGE_GOLOCATE at the result, so nothing compiles at request time. Off
    the image (tests, the demo scripts) we build it into a temp dir once per
    machine and reuse it -- `go build` is ~200ms and would otherwise be paid
    on every single file parsed.
    """
    global _locator_bin
    prebuilt = os.environ.get("BUGFORGE_GOLOCATE")
    if prebuilt:
        return prebuilt
    if _locator_bin and Path(_locator_bin).exists():
        return _locator_bin

    name = "golocate.exe" if sys.platform == "win32" else "golocate"
    out_dir = Path(tempfile.gettempdir()) / "bugforge-golocate"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / name

    if not out.exists():
        proc = subprocess.run(
            [go, "build", "-o", str(out), "."],
            cwd=_LOCATOR_SRC,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise GoToolchainError(f"could not build the golocate helper: {proc.stderr.strip()}")
    _locator_bin = str(out)
    return _locator_bin


def _call_locator(mode: str, path: str, source: str, go: str = "go") -> dict:
    payload = json.dumps({"path": path, "source": source})
    proc = subprocess.run(
        [locator_binary(go), f"-mode={mode}"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        # Non-zero means the helper itself broke. A Go syntax error is a
        # normal answer and comes back as {"error": ...} with status 0.
        raise GoToolchainError(f"golocate -mode={mode} failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


def check_syntax(source: str, path: str) -> None:
    """mutate.splice's validator for Go. Raises MutationError if it won't parse.

    This catches a splice that produced nonsense (an operator deleted into
    `if  {`). It is a *parse* check, not a type check: `return "" - 1` parses
    fine and fails at build time instead, which run_tests reports as a build
    failure and Phase 3 drops as catastrophic.
    """
    result = _call_locator("check", path, source)
    if result.get("error"):
        raise _mutate.MutationError(f"mutation at {path} produced invalid syntax: {result['error']}")


# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------


def is_mutable_source_path(path: str) -> bool:
    """File-level filter: skip tests, vendored code, fixtures and docs."""
    norm = path.replace("\\", "/")
    parts = norm.split("/")
    filename = parts[-1]
    if _SKIP_DIRS & set(parts[:-1]):
        return False
    if not filename.endswith(SOURCE_SUFFIX):
        return False
    # Both halves of Go's test convention: `foo_test.go` in the package, and
    # the `_test` package suffix files that sit beside it.
    if filename.endswith("_test.go"):
        return False
    return True


# --------------------------------------------------------------------------
# talking to `go`
# --------------------------------------------------------------------------


def _go(args: list[str], cwd: Path, runner: RunnerConfig, timeout: int | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_go_exe(runner), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        # Environment inherited unchanged and deliberately so. The knobs this
        # needs -- GOCACHE and GOPATH somewhere writable, CGO_ENABLED=0 for a
        # pure-Go build -- are properties of where it runs, not of what it is
        # doing, so the image sets them (infra/docker/Dockerfile.go) and a
        # developer's machine keeps its own. Forcing CGO_ENABLED=0 here was
        # the earlier version and it broke every run on a Windows host whose
        # Application Control policy refuses to execute the resulting binary.
    )


def module_path(repo: Path, runner: RunnerConfig) -> str:
    """The module's import path, e.g. "github.com/golang-jwt/jwt/v5".

    Coverage profiles name files by import path, not by a path relative to the
    repo, so every profile line has to have this prefix stripped before it can
    be matched against a MutationSite.
    """
    proc = _go(["list", "-m"], repo, runner)
    if proc.returncode != 0:
        raise GoToolchainError(f"`go list -m` failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout.strip().splitlines()[0].strip()


def _packages(repo: Path, runner: RunnerConfig) -> list[tuple[str, str]]:
    """[(import path, repo-relative dir)] for every package in the module."""
    proc = _go(["list", "-f", "{{.ImportPath}}\t{{.Dir}}", "./..."], repo, runner)
    if proc.returncode != 0:
        raise _baseline.BaselineError(f"`go list ./...` failed: {proc.stderr.strip()}")
    out = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        import_path, directory = line.split("\t", 1)
        rel = Path(directory.strip()).resolve().relative_to(repo.resolve()).as_posix()
        out.append((import_path.strip(), rel or "."))
    return out


def _profile_to_lines(profile: str, module: str) -> dict[str, set[int]]:
    """Parse a coverprofile into `repo-relative path -> covered line numbers`.

    Go reports basic *blocks*, not lines: one entry spans from the start of a
    block to its end, and every line in between ran together. Expanding the
    span is therefore accurate rather than an approximation -- with the one
    caveat that a block's closing line is shared with whatever follows it.
    """
    prefix = module + "/"
    covered: dict[str, set[int]] = {}
    for line in profile.splitlines():
        m = _PROFILE_RE.match(line.strip())
        if m is None:
            continue  # the "mode: set" header, or a blank line
        import_file, start, end, count = m.groups()
        if int(count) == 0:
            continue
        if import_file == module:
            rel = ""
        elif import_file.startswith(prefix):
            rel = import_file[len(prefix) :]
        else:
            continue  # a dependency outside the module; not mutable anyway
        if not rel:
            continue
        covered.setdefault(rel, set()).update(range(int(start), int(end) + 1))
    return covered


def _list_tests(repo: Path, pkg: str, runner: RunnerConfig) -> list[str]:
    proc = _go(["test", "-list", "^Test", pkg], repo, runner)
    if proc.returncode != 0:
        return []
    # Benchmarks, examples and fuzz targets are deliberately out of scope: an
    # example's assertion is its Output comment and a fuzz target has no fixed
    # verdict, so neither gives the clean pass/fail a challenge needs.
    return _LIST_TEST_RE.findall(proc.stdout)


def _parse_go_test_output(output: str) -> tuple[int, int, int, list[str]]:
    passed = failed = 0
    failing: list[str] = []
    for verdict, name in _RESULT_RE.findall(output):
        if verdict == "PASS":
            passed += 1
        elif verdict == "FAIL":
            failed += 1
            # Subtests report as "TestParent/case"; the parent is reported
            # too, so keep the parent only -- it is the id the baseline knows.
            failing.append(name.split("/", 1)[0])
    # A build failure is not a test failure. It means the mutation did not
    # compile, which Phase 3 drops as catastrophic rather than scoring.
    errors = 1 if (_BUILD_FAIL_RE.search(output) and passed == 0 and failed == 0) else 0
    # dict.fromkeys: dedupe the parent names subtests produced, order kept.
    return passed, failed, errors, list(dict.fromkeys(failing))


def run_go_tests(
    tree: Path, test_ids: list[str] | None, runner: RunnerConfig
) -> _runner.RunResult:
    """Runs `test_ids` (or the whole module when falsy) inside `tree`.

    Ids are grouped by package so a run spanning two packages costs two
    `go test` invocations rather than one per test, and each gets an anchored
    `-run` alternation so `TestParse` never also matches `TestParseUnverified`.
    """
    groups: dict[str, list[str]] = {}
    for test_id in test_ids or []:
        pkg_dir, _, name = test_id.rpartition(":")
        groups.setdefault(pkg_dir or ".", []).append(name)

    if not groups:
        # One command per package rather than a single `./...`. `go test`
        # prints results without saying which package each came from, so a
        # combined run hands back bare "TestParser" while the baseline is
        # keyed "`.:TestParser`" -- the ids then match nothing, which showed
        # up downstream as every challenge scoring search_space 0. Running per
        # package is how the package half of the id is known. The build cache
        # makes the extra invocations near-free.
        commands: list[tuple[list[str], str | None]] = [
            (["test", "-v", "-count=1", import_path], pkg_dir)
            for import_path, pkg_dir in _packages(Path(tree), runner)
        ]
    else:
        commands = [
            (
                [
                    "test",
                    "-v",
                    "-count=1",
                    "-run",
                    "^(" + "|".join(sorted(set(names))) + ")$",
                    "." if pkg_dir == "." else "./" + pkg_dir,
                ],
                pkg_dir,
            )
            for pkg_dir, names in sorted(groups.items())
        ]

    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    passed = failed = errors = 0
    failing: list[str] = []
    returncode = 0

    for args, pkg_dir in commands:
        try:
            proc = _go(args, tree, runner, timeout=runner.timeout_s)
        except subprocess.TimeoutExpired as e:
            return _runner.RunResult(
                returncode=-1,
                stdout=_as_text(e.stdout),
                stderr=_as_text(e.stderr),
                passed=0,
                failed=0,
                errors=0,
                timed_out=True,
                collection_error=False,
                failing_tests=[],
            )
        stdout = relativize(proc.stdout, tree)
        stderr = relativize(proc.stderr, tree)
        stdout_parts.append(stdout)
        stderr_parts.append(stderr)
        returncode = returncode or proc.returncode
        p, f, e_count, names = _parse_go_test_output(stdout + "\n" + stderr)
        passed += p
        failed += f
        errors += e_count
        # Re-qualify the bare names `go test` printed back into full test ids.
        prefix = f"{pkg_dir}:" if pkg_dir else ""
        failing.extend(prefix + n for n in names)

    return _runner.RunResult(
        returncode=returncode,
        stdout="\n".join(stdout_parts),
        stderr="\n".join(stderr_parts),
        passed=passed,
        failed=failed,
        errors=errors,
        timed_out=False,
        collection_error=errors > 0 and passed == 0 and failed == 0,
        failing_tests=failing,
    )


def relativize(text: str, tree: Path) -> str:
    """Rewrites absolute paths into `tree` as repo-relative ones.

    Go panic stacks name files by absolute path, and the tree a mutation runs
    in is a temp directory. Two reasons that has to go:

    * the traceback is handed to the learner, and
      `C:/Users/.../bugforge-mutation-wdme6jy7/tree/parser.go` both leaks the
      build machine's filesystem and is not a path they can open;
    * scoring compares a frame's file against the mutated file, which is
      repo-relative -- so without this every frame looks like a different file
      and `displacement` silently falls back to its maximum, which is a wrong
      score rather than a visible failure.

    Both separator spellings are handled: the paths come from whichever OS ran
    the suite, and a Go stack on Windows mixes the two in the same dump.
    """
    root = str(Path(tree).resolve())
    variants = {root, root.replace("\\", "/"), root.replace("/", "\\")}
    for variant in variants:
        for trailing in ("/", "\\"):
            text = text.replace(variant + trailing, "")
    return text


def _as_text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", "replace")


def compute_go_baseline(repo: Path, runner: RunnerConfig, use_cache: bool = True) -> Baseline:
    """One coverage profile per test, unioned into a line->tests map.

    Cached under the same `(repo, commit_sha)` key as the Python baseline,
    because this is the slowest thing BugForge does: on the vetted repo it is
    ~3s per test, so a few minutes on a cold commit and nothing thereafter.
    """
    repo = Path(repo).resolve()
    commit_sha = _baseline.git_head_sha(repo)
    cache_file = _baseline.cache_path(repo.name, commit_sha)
    if use_cache and cache_file.exists():
        return Baseline(**json.loads(cache_file.read_text(encoding="utf-8")))

    module = module_path(repo, runner)

    # The green gate -- and the reason it runs the whole suite rather than
    # trusting the per-test runs below: a test that only passes in isolation
    # is not a green suite, and every later verdict assumes a green suite.
    try:
        whole = _go(["test", "-count=1", "./..."], repo, runner, timeout=runner.timeout_s)
    except subprocess.TimeoutExpired:
        raise _baseline.BaselineError(
            f"`go test ./...` did not finish within {runner.timeout_s}s in {repo}. "
            "A repo whose own suite does not fit in one invocation cannot be baselined "
            "here: the per-test coverage pass below runs it once per test."
        ) from None
    if whole.returncode != 0:
        raise _baseline.BaselineError(
            f"`go test ./...` is not green in {repo}; a red baseline makes every "
            f"later verdict meaningless.\n{whole.stdout[-2000:]}\n{whole.stderr[-2000:]}"
        )

    line_to_tests: dict[str, list[str]] = {}
    total_tests = 0
    for import_path, pkg_dir in _packages(repo, runner):
        for name in _list_tests(repo, import_path, runner):
            total_tests += 1
            test_id = f"{pkg_dir}:{name}"
            text = ""
            with tempfile.TemporaryDirectory(prefix="bugforge-gocov-") as tmp:
                profile = Path(tmp) / "cover.out"
                try:
                    proc = _go(
                        [
                            "test",
                            "-count=1",
                            "-covermode=set",
                            # Without -coverpkg the profile only covers the
                            # package under test, so a `request` test exercising
                            # root-package code would leave those lines looking
                            # untested -- and every mutation there would be
                            # misfiled as a test gap.
                            "-coverpkg=./...",
                            f"-coverprofile={profile}",
                            "-run",
                            "^" + name + "$",
                            import_path,
                        ],
                        repo,
                        runner,
                        timeout=runner.timeout_s,
                    )
                except subprocess.TimeoutExpired:
                    # One slow test costs its own coverage rows, not the whole
                    # map. Lines only it covers end up looking untested, which
                    # files them as test gaps -- wrong, but conservative: a
                    # false gap is a report nobody acts on, where a false
                    # challenge is a bug a learner cannot find.
                    continue
                if proc.returncode == 0 and profile.exists():
                    text = profile.read_text(encoding="utf-8", errors="replace")
            if not text:
                continue
            for rel_path, linenos in _profile_to_lines(text, module).items():
                if not is_mutable_source_path(rel_path):
                    continue
                for lineno in linenos:
                    line_to_tests.setdefault(f"{rel_path}:{lineno}", []).append(test_id)

    if not line_to_tests:
        raise _baseline.BaselineError(
            f"no coverage was recorded for {repo}. Without a line->tests map Phase 3 "
            "cannot pick which tests to run for a mutation."
        )

    result = Baseline(
        repo=repo.name,
        commit_sha=commit_sha,
        total_tests=total_tests,
        green=True,
        line_to_tests={k: sorted(set(v)) for k, v in sorted(line_to_tests.items())},
    )
    _baseline.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return result


# A goroutine stack pair's location half: a TAB, then "parser.go:80 +0x4a5".
_STACK_LOCATION = re.compile(r"^\t(.+\.go):(\d+)(?: \+0x[0-9a-f]+)?$")
# "    validator_test.go:123: Expected true, got false" -- a t.Errorf report.
_REPORT = re.compile(r"^\s+(\S+\.go):(\d+): ?(.*)$")
_GOROUTINE = re.compile(r"^goroutine \d+ \[[^\]]*\]:$")


def _is_in_repo(path: str) -> bool:
    """True for a path `relativize` turned repo-relative.

    Anything still absolute -- a GOROOT frame, a module-cache frame -- is code
    the learner was never given and cannot have broken.
    """
    norm = path.replace("\\", "/")
    return not norm.startswith("/") and ":" not in norm.split("/")[0]


def _short_func(line: str) -> str:
    """`.../v5.(*Parser).Parse(0x1)` -> `(*Parser).Parse`.

    Only the trailing argument list is stripped, anchored at the end. Cutting
    from the first "(" would eat a pointer receiver, because the first
    parenthesis in that name opens `(*Parser)` rather than the arguments.
    """
    without_args = re.sub(r"\([^()]*\)$", "", line.strip())
    tail = without_args.rsplit("/", 1)[-1]
    package, _, rest = tail.partition(".")
    return rest or package


def _failure_text(lines: list[str], short_name: str) -> str:
    """The slice of output belonging to the failing test, for traceback.txt.

    Starts at that test's own `=== RUN` line and ends at its package's
    verdict, so a learner reads one failure rather than the whole suite's log.
    """
    start = 0
    for i, line in enumerate(lines):
        if line.startswith("=== RUN") and line.rstrip().endswith(short_name):
            start = i
            break
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("ok \t") or lines[i].startswith("FAIL\t"):
            end = i + 1
            break
    return "\n".join(lines[start:end])


def parse_go_failure(output: str, test_id: str) -> tuple[list[tuple[str, int, str]], str]:
    """`(frames, raw text)` for one failing Go test.

    Go produces two quite different failure shapes and a real challenge can be
    either: a panic carrying a genuine goroutine stack, or a bare `t.Errorf`
    report with no stack at all. Both are handled here.

    Frames come back **outermost first**, the order a Python traceback already
    has -- a goroutine stack is printed innermost first, so it is reversed.

    Only in-repo frames are returned. The deepest frames of a panic are the
    runtime's own unwinding (`panic.go`, `testing.tRunner`), and counting them
    would put three frames between the failure and a defect that is in fact
    sitting exactly where it blew up -- scoring it as displacement 3 instead
    of 0. The raw text keeps them, because the solve screen shows them dimmed
    on the rail and the shape of the stack should stay honest.
    """
    lines = output.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    short_name = test_id.rpartition(":")[2] or test_id

    stack: list[tuple[str, int, str]] = []
    reports: list[tuple[str, int, str]] = []
    pending_func = ""
    in_stack = False

    for raw in lines:
        if _GOROUTINE.match(raw):
            in_stack = True
            continue
        if in_stack:
            # The stack ends at a blank line, or at "created by ..." -- which
            # introduces the goroutine's spawn site, always the same
            # scaffolding rather than part of this call chain.
            if raw.strip() == "" or raw.startswith("created by "):
                in_stack = False
                continue
            location = _STACK_LOCATION.match(raw)
            if location:
                stack.append((location.group(1), int(location.group(2)), pending_func))
                pending_func = ""
            elif not raw.startswith("\t"):
                pending_func = _short_func(raw)
            continue
        report = _REPORT.match(raw)
        if report:
            reports.append((report.group(1), int(report.group(2)), ""))

    frames = list(reversed(stack)) if stack else reports
    return [f for f in frames if _is_in_repo(f[0])], _failure_text(lines, short_name).strip()


class GoAdapter:
    """Go source, `go test` for tests, one coverage profile per test for the map."""

    name = "go"

    def source_root(self, tree: Path, package: str) -> Path:
        # The whole module. `package` here is the go.mod module path
        # ("github.com/golang-jwt/jwt/v5"), which names no directory on disk --
        # the image build checks it matches `go list -m` instead.
        return Path(tree)

    def discover_sources(self, repo: Path) -> list[Path]:
        repo = Path(repo)
        found = []
        for path in sorted(repo.rglob("*" + SOURCE_SUFFIX)):
            rel = path.relative_to(repo).as_posix()
            if not is_mutable_source_path(rel):
                continue
            # Generated code compiles and runs like any other source, but a
            # learner sent to fix it would be fixing the wrong file: the defect
            # belongs in the generator. Cheap to detect, because Go's
            # convention for saying so is machine-readable.
            head = path.read_text(encoding="utf-8", errors="replace")[:4096]
            if _GENERATED_RE.search(head):
                continue
            found.append(path)
        return found

    def find_candidates(self, source: str, path: str) -> list[MutationSite]:
        if not is_mutable_source_path(path):
            return []
        result = _call_locator("locate", path, source)
        if result.get("error"):
            return []  # unparseable file; nothing to locate
        sites = [
            MutationSite(
                path=raw["path"],
                lineno=raw["lineno"],
                col_start=raw["col_start"],
                col_end=raw["col_end"],
                operator_id=raw["operator_id"],
                original_token=raw["original_token"],
                mutated_token=raw["mutated_token"],
                enclosing_function_name=raw["enclosing_function_name"] or None,
                enclosing_class_name=raw["enclosing_class_name"] or None,
            )
            for raw in result.get("sites", [])
        ]
        # Same cap and the same fixed stride as the Python adapter, so "25 per
        # file" means the same thing in both languages.
        if len(sites) > _mutate.MAX_CANDIDATES_PER_FILE:
            stride = len(sites) / _mutate.MAX_CANDIDATES_PER_FILE
            sites = [sites[int(i * stride)] for i in range(_mutate.MAX_CANDIDATES_PER_FILE)]
        return sites

    def apply(self, source: str, site: MutationSite) -> str:
        return _mutate.splice(source, site, check_syntax)

    def baseline(self, repo: Path, runner: RunnerConfig) -> Baseline:
        return compute_go_baseline(Path(repo), runner, use_cache=runner.use_cache)

    def coverage_map(self, repo: Path, runner: RunnerConfig) -> LineToTests:
        return self.baseline(repo, runner).line_to_tests

    def run_tests(
        self, tree: Path, test_ids: list[str] | None, runner: RunnerConfig
    ) -> _runner.RunResult:
        return run_go_tests(Path(tree), test_ids, runner)

    def extract_failure(self, output: str, test_id: str) -> tuple[list[tuple[str, int, str]], str]:
        return parse_go_failure(output, test_id)
