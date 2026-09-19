# BugForge — what is built, phase by phase

BugForge turns a vetted open-source repo into debugging practice. It mutates
one token of real source, keeps the mutation only if the repo's **own** test
suite catches it, and hands you the broken tree plus the stack trace. Every
mutation the suite *misses* becomes a test-gap report for the maintainers.

**Python and Go**, via the adapter seam in `bugforge/languages/`. Everything
outside that package names a language exactly nowhere.

No model picks, scores, or grades anything. There is exactly one model call in
the whole system, and it only writes a title and two sentences of prose.

**Status:** Phases 1–9 are code-complete and tested locally. **Nothing is
deployed.** There are no AWS credentials on the build machine, so the stack has
never been created and the live URL does not exist yet. See
[Deploying](#deploying).

---

## At a glance

| Phase | What it does | State |
|---|---|---|
| 1 | Baseline: run the suite once, map every line to the tests that cover it | done, tested |
| 2 | Mutation: locate tokens by AST, splice bytes surgically | done, tested |
| 3 | Selection: classify, score, package challenges and test gaps | done, tested |
| 4 | AWS: Lambda + Step Functions + DynamoDB + S3 + HTTP API (SAM) | code done, **never deployed** |
| 5 | Describer: one model call for a title and two sentences, with a deterministic fallback | done, tested; **Bedrock output never exercised** |
| 6 | Web app: landing, repos, course, cards, solve, result, gaps | done, verified in a real browser |
| 7 | Investigation replay on the result screen | done, verified in a real browser |
| 8 | GitHub sign-in: identity, server-side progress, gated submissions | code done, **never deployed**; needs a real OAuth app |
| 9 | Go: second language adapter, second vetted repo, per-language grading | pipeline verified locally; **image never built** |

Tests: **363 Python tests**, **109 web tests**, all passing.

---

## Phase 1 — Baseline

`bugforge/baseline.py`

- Runs the repo's full test suite once with `coverage` **contexts** enabled.
- Reads the resulting `.coverage` SQLite database directly to build a
  `line -> tests that execute it` map. This is what lets Phase 3 run only the
  handful of tests relevant to a mutation instead of the whole suite.
- Refuses to continue if the suite isn't green to begin with, or if coverage
  contexts came back empty (`BaselineError`) — a red baseline would make every
  later verdict meaningless.
- Caches the result as JSON keyed on `(repo, commit_sha)` in `.baseline_cache/`,
  so the slow step never runs twice for the same commit.

## Phase 2 — Mutation

`bugforge/mutate.py`

- The AST is used **only to locate** tokens (line + byte offsets). Application
  never calls `ast.unparse`: it slices the exact byte span on one line and
  splices the replacement in, so comments, formatting, and every unrelated line
  survive untouched.
- All column offsets are **UTF-8 byte offsets**, matching what Python's `ast`
  reports — mixing these up with character offsets is the easy way to silently
  corrupt any file containing a non-ASCII character, so the module works on
  `bytes` throughout.
- Seven operators: `COMPARISON`, `ARITHMETIC`, `BOOLEAN`, `BOUNDARY`,
  `NEGATION`, `RETURN`, `DEFAULT_ARG`.
- Skips what shouldn't be mutated: `tests/`, `test/`, `docs/`, `conftest.py`,
  `setup.py`, and `__repr__`/`__str__` bodies. Caps at 25 candidates per file.
- Verifies after applying that exactly one line changed, or raises
  `MutationError`.

## Phase 3 — Selection, scoring, packaging

`bugforge/select.py`, `bugforge/runner.py`, `bugforge/package.py`

Per candidate, in order:

1. Run **only** the tests the baseline says cover the mutated line.
2. Timeout (30s) → drop `timeout`.
3. Collection/import error → drop `catastrophic`.
4. No covering test failed → **TEST_GAP** → goes to the maintainer report.
5. Otherwise run the full suite once (for accurate counts and a clean
   traceback). More than 25% of the suite red → drop `too_loud`.
6. Score it. Admit only `score >= 3.0`, else drop `low_score`.

**The score** (deterministic, no model involved), from three inputs:

- **displacement** — how many stack frames sit between where the test fails and
  where the defect actually is (capped at 4).
- **search space** — how many source files the failing test executes (capped at
  20).
- **noise** — what fraction of the suite goes red; *fewer* red tests is harder.

`raw = 1 + 9 * (0.45·d + 0.25·s + 0.30·n) - name_leak`, clamped to 1–10. The
`name_leak` penalty applies when the failing test's name gives away the mutated
function.

**Packaging** produces, per admitted mutation: a learner-facing tree with fresh
git history and a single commit plus `traceback.txt` (`-public.tar.gz`), a
separate answers bundle containing only the mutation patch
(`-answers.tar.gz`), and the challenge record JSON. Licence and README files are
preserved in the tree.

## Phase 4 — AWS

`infra/template.yaml` (SAM), `infra/statemachine/forge_repo.asl.json`,
`infra/docker/`, `cloud/`

- **28 resources**: 10 Lambda functions, 5 DynamoDB tables (challenges,
  submissions, gap reports, leaderboard, progress), 3 Secrets Manager secrets,
  1 S3 bucket, 1 HTTP API, 1 state machine, and a **separate IAM role per
  function**. (22 of those predate Phase 8; sign-in added six.)
- **Workflow**: `Baseline → Generate → Map(15 parallel batches) → Score →
  Describe → Persist`. `fn_score` loops back into itself on a remaining-time
  budget, because one full-suite run per survivor does not fit in a single
  300s Lambda.
- **`ToleratedFailurePercentage: 90` is deliberate.** A batch raises when none
  of its mutations survived, and that is the *normal* outcome — most mutations
  are test gaps. Every batch writes its per-mutation results to S3 *before*
  deciding whether to raise, so a failed branch loses nothing.
- **Answers are isolated.** Only `fn_reveal`'s role can read `answers/`. The API
  role cannot read that prefix at all, and `GET /challenges/{id}/tree` refuses
  to presign any key outside the public prefix (checked a second time at the
  call site, because a mistake there is unrecoverable).
- **Vetted repos only.** `infra/docker/vetted_repos.json` is the allowlist; one
  pre-built image per repo with dependencies installed at build time on a
  trusted machine, pinned to a commit SHA. Nothing is ever cloned or
  `pip install`ed at runtime. Each entry names its `language`, which picks
  both the adapter and the Dockerfile. Currently vetted:
  `jd/tenacity` @ `3e58094d` (Python) and `golang-jwt/jwt` @ `e9547a11` (Go).
- **Anti-cheat** (`cloud/anti_cheat.py`): rejects patches touching test files or
  non-Python files, and fingerprints the patched tree against the original to
  catch a patch that edits something it didn't declare. On top of the path
  rules it runs all four AST hygiene checks — a patch is rejected if it deletes
  an `assert`, adds a swallowing `except: pass`, adds a
  `@pytest.mark.skip`/`skipif`/`xfail` marker, or adds a call to `sys.exit`,
  `os._exit`, `pytest.exit`, `exit`, `quit`, or `os.abort`. The comparison is
  against the original tree's own counts rather than an absolute threshold,
  because repos legitimately contain bare excepts and `xfail` markers already.
  All of it is AST-based: a regex would be both too eager (the string
  `sys.exit` inside a docstring) and too easy to slip past (`except  :`, line
  continuations, `exec`).

**API**: `POST /forge`, `GET /forge/{execution_id}`, `GET /repos`,
`GET /challenges`, `GET /challenges/{id}`, `GET /challenges/{id}/tree`,
`POST /submissions`, `GET /submissions/{id}`, `GET /submissions/{id}/reveal`,
`GET /gaps`, `GET /leaderboard`, `GET /me/progress`, plus the four sign-in
routes (Phase 8). Only `POST /submissions` requires a session.

The live generation stream masks the location of every **kept** or
still-scoring mutation (`░░░░░░.py:░░░`) while showing rejects and test gaps in
full — a learner who reads the location off the stream has already solved it.
Challenge IDs are opaque for the same reason: repo + commit + a SHA-256 digest
of the site, never the file name or line number.

## Phase 5 — The describer

`cloud/describe.py`

- The system's **only** model call: a bug-ticket title and two sentences.
- The deterministic template fallback is built **first**, so prose always
  exists. `BUGFORGE_DISABLE_BEDROCK` skips the model entirely (the `anthropic`
  SDK is never even imported).
- Output is post-checked in code: any description naming a file path, a line
  number, or any identifier from the mutated line's enclosing scope, the module
  path, or the failing test's id is **rejected**. One retry, then fallback.
- A failed API call (no credentials, throttled, SDK missing) falls back
  immediately.

> **Not yet exercised:** with no AWS credentials, every challenge produced so
> far has used the template. The Bedrock path has never produced real output.

## Phase 6 — The web app

`web/` — Next.js App Router (static export), Tailwind v4, CodeMirror 6. No
component library.

**Design system, enforced at the token level.** `app/globals.css` deletes
Tailwind's default colour, font, radius, and shadow scales outright, so a
rounded corner, a shadow, a sans-serif face, or an off-palette colour *cannot
be written as a utility class at all*. JetBrains Mono everywhere including
headings; 1px hairline borders; square corners; 120ms fades and a blinking
block cursor as the only motion.

### Screen 1 — Landing
- The input is the hero: a single wide field prefixed `github.com/`, plus three
  clickable example repos.
- Accepts whatever people paste (`owner/repo`, a full URL, `.git`, SSH form).
- Refuses an unvetted repo **instantly** from the known list, and explains why
  images are built ahead of time, before the API is even called.
- The generation stream is live terminal output polled from
  `GET /forge/{execution_id}`, showing **rejections, not just successes**:
  kept mutations, dropped ones with the reason (`too loud`, `too easy`,
  `timeout`, `import error`), and test gaps. It ends with
  `N challenges ready · M test gaps found`, with a running tally of
  kept/dropped/gaps and a legend.

### Screen 2 — Repos
- A dense table, not cards: repo, challenge count, difficulty histogram as small
  bars, average score, gaps.
- Opening a repo frames it as a course: *"learn tenacity in 20 bugs"*, ordered
  easiest first, with difficulty filters and a solved counter.

### Screen 3 — Challenge cards
- Difficulty label, language, the evocative generated name, the two-sentence
  symptom, repo + licence.
- Difficulty is **three small vertical bars** (displacement / search space /
  noise), each with a tooltip explaining what it measures and its actual value.
  Never a single opaque number.

### Screen 4 — Solve
- **Left — the traceback as a clickable spine.** Each frame is a row on a violet
  rail. Clicking opens that file at that line; visited frames get a filled
  marker; the frame that raised is amber; frames outside the repo (stdlib,
  site-packages) stay on the rail, dimmed and unclickable, so the shape of the
  stack stays honest. When the trace never leaves the test, it says so and tells
  you to follow what the test calls.
- **Centre — CodeMirror 6**, dark, line numbers, tabs accumulating as files
  open. Trace frames are marked in the gutter and **follow your edits** (markers
  are mapped through document changes, so they stay on the right line after you
  insert above them). Syntax highlighting is deliberately monochrome — weight
  and dimness, not hues — so that amber (where it failed) and violet (the trace)
  are the only colours that carry meaning. Test files and non-Python files open
  read-only, because the grader would reject a patch touching them anyway.
- **Right** — description, difficulty bars, failing test names (click one to
  jump to its `def`), a monospace timer, the keybinding legend, and the file
  tree (files in the trace marked violet, modified files marked `M`).
- **Bottom** — the status bar:
  `tenacity/boolean-in-retry · 04:31 · 3 files open · 1 modified · ⌘↵ submit`.
- **Keybindings that actually work**: `⌘↵` submit, `⌥[` / `⌥]` walk the trace,
  `⌥W` close tab, `⌘S` save draft, `⌘F` find. They're bound in the capture phase
  so they win over CodeMirror's own bindings (`⌘↵` does not insert a newline).
- **Drafts survive a reload**: edits, open tabs, visited frames, and the clock
  are all kept per challenge in the browser.
- **Submission log** as terminal output: the patch stats, then `PASS`, `FAIL`
  with the still-red test names (clickable), or `REJECTED` with the anti-cheat
  reason. Submitting with nothing changed is blocked client-side before it is
  sent.

### Screen 5 — Result
- `PASS — 2,847 tests green` in terminal style, with the time taken.
- **The payoff**: the single changed token at display size — `or → and`. The
  size adapts to the token, because most real mutations replace a whole returned
  expression, and some delete a token entirely (shown as `∅ nothing`).
- Then the changed line with the token boxed, labelled *original* vs *what you
  were given*; the full mutation diff with per-side line numbers; and your own
  fix, both collapsible.
- Then the repo reveal — repo, commit, file:line, licence — a link to the real
  file on GitHub at that line, and the next bug in the course.

### Screen 6 — Gaps
- The maintainer report: file, line, the untested mutation, the enclosing
  function, how many tests cover the line, and why it matters. Deliberately
  plain — it is a report, not a dashboard.

## Phase 7 — Investigation replay

`web/lib/replay.ts`, `web/components/result/Replay.tsx`,
`cloud/handlers/fn_api.py`

- The solve screen logs `{file, opened_at}` every time you open a file, and
  **POSTs the log with the submission**. The server sanitises it (capped at 300
  entries, anything malformed dropped) and stores it on the submission item;
  `fn_grade` merges its verdict into that same item, so the log survives
  grading and comes back from `GET /submissions/{id}`.
- **The log is display data only.** It is never passed to the grader and cannot
  influence a verdict.
- On the result screen it is drawn as a **horizontal timeline in pure SVG** — no
  charting library. One lane per file; the white line is where you were, moving
  between lanes over time; the **violet line is the true causal path**, from the
  frame that raised up the trace to the file that was actually mutated. The gap
  between the two lines is the lesson.
- The mutated file's lane is tinted amber and labelled; the causal path is
  marked `raised here` at one end and `the bug` at the other; per-file dwell
  times sit in their own right-hand gutter. The chart is drawn at exact pixel
  width (not scaled), so 11px labels stay 11px, and the time axis thins out
  rather than overlapping on a phone.
- Beneath it, one generated line:
  > You spent 5 of 11 seconds in tenacity/\_\_init\_\_.py. The bug was in
  > tenacity/retry.py, two frames upstream.

  It has cases for reading the right file all along, for never opening the
  broken file at all, and for submitting without opening anything.
- If more than 12 files were opened, the overflow collapses into one shared
  "N other files" lane so the line stays continuous and readable.

---

---

---

## Phase 8 — GitHub sign-in

`cloud/auth.py`, `cloud/handlers/fn_auth.py`, `cloud/progress.py`,
`web/lib/session.ts`, `web/components/SignIn.tsx`

The OAuth **web flow**, on our own Lambda. No Cognito, no new managed service:
one function, three routes, two secrets.

    GET  /auth/github    -> 302 to github.com, signed state in a 10-minute cookie
    GET  /auth/callback  <- code -> token -> profile -> session cookie -> 302 back
    POST /auth/logout    -> clears the cookie
    GET  /auth/me        -> the profile, or {"user": null}

### What signing in changes

Three things, and nothing else. **Submissions are attributed to you** — `POST
/submissions` is the one gated route. **Your solved challenges follow you**
(`ProgressTable`, `GET /me/progress`), instead of living only in one browser's
localStorage. **The leaderboard shows a GitHub handle and avatar** instead of
an opaque id. Browsing repos, opening a course, reading a traceback, editing
and the whole gap report stay open, signed out.

### The parts that are security, not plumbing

- **A session is signed, not stored.** An HS256 JWT holding the GitHub numeric
  id, login and avatar. No session table, nothing to revoke, and no DynamoDB
  round trip per request — a session grants exactly one thing, and that is
  worth less than the latency. `alg` is never read back out of the token
  (the "alg: none" forgery), verification is `hmac.compare_digest`, and a
  missing, expired, malformed or foreign-signed token is indistinguishable
  from being signed out.
- **The user id comes from the session, never the body.** It is
  `gh:<numeric id>` — the id, not the login, because logins are renameable and
  a renamed login would otherwise inherit someone else's solved history. This
  also closes a real hole in the pre-auth code, where `POST /submissions` took
  `user_id` from the request body: anyone could have written points into
  anyone's leaderboard row.
- **`state` is signed *and* echoed in a cookie**, and both must match. Without
  it an attacker can complete a sign-in inside a victim's browser with their
  own GitHub code, silently attaching the victim's work to their account.
- **The callback can only redirect to our own origin.** An open redirect on an
  OAuth callback is how a login flow becomes a phishing primitive, so anything
  that is not a same-origin absolute URL or a plain relative path becomes the
  app root.
- **Two secrets, two blast radii.** The GitHub client secret can impersonate
  the whole application to GitHub, so **only fn_auth's role can read it** —
  that role has no S3, no DynamoDB and no Lambda invoke at all. The session
  signing key can do nothing but mint and verify sessions for this stack, so
  fn_api gets it too, because it has to verify a cookie on every gated
  request. It is generated by Secrets Manager rather than passed in: nobody
  needs to know that value, so nobody should ever have typed it.
- **A solve is scored once.** `progress.record_solve` writes conditionally on
  `attribute_not_exists(challenge_id)`, and points are awarded **only when that
  write succeeds**. Without it, resubmitting the same accepted fix is an
  unbounded score.
- **Failing closed is not failing over.** A stack deployed with no auth
  secrets is fully usable signed out: every route answers, `GET /auth/me`
  returns `{"user": null}`, and only submitting is refused. Sign-in is an
  enhancement; no page fails to render because of it.

### The cookie is cross-site, and that has consequences

The web app is on Amplify and the API is on execute-api — different
registrable domains. So the session cookie is `HttpOnly; Secure;
SameSite=None`, every request from the client sends `credentials: "include"`,
and the API answers `Access-Control-Allow-Credentials: true`. **`AllowCredentials`
is incompatible with `AllowOrigins: ["*"]` by spec**, so deploying sign-in
*requires* a real `WebOrigin`. With `WebOrigin="*"` the stack still deploys and
still works — signed out.

Because the cookie is HttpOnly, nothing in the browser can read it; the only
way the app learns who you are is `GET /auth/me`. `web/lib/session.ts` caches
that one answer in a module-level promise shared by every subscriber, so the
header, the course page and the solve screen cost one request between them.

### Solved state: merged, not chosen between

Local and server sets are unioned. The common path is solving a few challenges
signed out and then signing in — dropping the local set at that moment would
look exactly like losing your work.

> **Not yet deployed.** There is no GitHub OAuth app and no callback URL,
> because the callback URL does not exist until the stack does. Everything
> above is unit-tested (61 tests across `tests/test_auth.py` and
> `tests/test_auth_api.py`, 9 in `web/lib/session.test.ts`), but no real GitHub
> round trip has ever happened.

## The language seam

`bugforge/languages/`

No language is hardcoded into the pipeline; each is an **adapter**. Everything
BugForge does to a repo is one of seven operations, and all seven are
language-specific, so they are named in one protocol
(`bugforge/languages/base.py`):

```python
class LanguageAdapter(Protocol):
    name: str
    def source_root(self, tree: Path, package: str) -> Path: ...
    def discover_sources(self, repo: Path) -> list[Path]: ...
    def find_candidates(self, source: str, path: str) -> list[MutationSite]: ...
    def apply(self, source: str, site: MutationSite) -> str: ...
    def baseline(self, repo: Path, runner: RunnerConfig) -> Baseline: ...
    def coverage_map(self, repo: Path, runner: RunnerConfig) -> LineToTests: ...
    def run_tests(self, tree: Path, test_ids: list[str] | None, runner: RunnerConfig) -> RunResult: ...
```

`PythonAdapter` (`languages/python.py`) owns **no logic** — every method
forwards to the phase 1–3 code that already existed (`baseline.py`,
`mutate.py`, `runner.py`), so calling through the adapter and calling the
function directly do exactly the same thing. That is asserted, not assumed:
`tests/test_languages.py` compares the two paths directly, so the wrapper
cannot quietly drift from what it wraps.

Callers ask `get_adapter(name)`; an unknown name raises
`UnsupportedLanguageError` naming what *is* available. Which adapter an image
uses comes from `REPO_LANGUAGE`, baked in at build time from
`vetted_repos.json` — the language and the image are the same choice, because
a Go image carries a Go toolchain and a Python image does not.

**What the seam was not.** Phase 4's handlers used to reach straight past it:
`fn_generate` called `mutate.find_candidates`, `fn_run_batch` and `fn_score`
called `runner.run_mutation`, `fn_grade` called `run_pytest`, `fn_baseline`
called `compute_baseline`. Each now goes through the adapter, and
`runner.run_mutation_with` is the language-neutral "materialize the mutation,
run these tests" they share. The `apply` half of Phase 2 was split the same
way: `mutate.splice` does the byte arithmetic for every language and takes the
syntax check as an argument, so Go reuses the offset logic rather than owning
a second copy of it — a second copy being exactly the silent corruption that
discipline exists to prevent.

## Phase 9 — Go

`bugforge/languages/go.py`, `bugforge/languages/golocate/`,
`infra/docker/Dockerfile.go`, `web/lib/lang.ts`, `web/lib/traceback-go.ts`

Second vetted repo: **`golang-jwt/jwt` @ `e9547a11` (v5.3.0)** — zero external
module dependencies, no cgo, no network in any test, 41 tests green in about
four seconds, MIT. Parsing and validation give the call depth a debugging
challenge needs; a flat conversion library would not.

### Locating tokens

Go's AST records the exact position of every operator (`BinaryExpr.OpPos`),
which Python's does not — so `golocate`, a small Go program built on `go/ast`,
is actually *more* precise than `mutate.py`, which has to search the gap
between two operands for its token. It speaks JSON over stdin/stdout and does
three things, all read-only: `locate` (emit positions), `check` (does this
parse?), and `fingerprint` (count what the anti-cheat compares). Splicing
stays in Python.

`go/token` reports a 1-based **byte** column, so converting to `MutationSite`'s
offsets is a subtraction and no rune decoding happens anywhere — the same
discipline `mutate.py` keeps, and `tests/test_languages_go.py` checks every
located span against the bytes it claims rather than one sample.

Operators mirror Python's table with three deliberate differences:

- **`!=` is mutable.** Python's table only flips `==`. `if err != nil` is the
  defining control-flow decision in Go source, and flipping it produces a
  defect that compiles, runs, and reads as a plausible human mistake.
- **`*` becomes `/`**, not floor division — Go's `/` is already integer
  division on integer operands.
- **No `DEFAULT_ARG`**, because Go has no default arguments. `RETURN` is
  restricted to flipping a bare `return true` / `return false`: Go's returns
  are typed and often multi-valued, so that is the only rewrite guaranteed to
  compile without consulting the type checker.

`NEGATION` is simpler than Python's: deleting the one `!` byte turns
`!(a || b)` into `(a || b)`, which is still valid Go, so the parenthesis
special case `mutate.py` needs does not arise.

A `+` with a string literal on either side is skipped — turning it into `-` is
a compile error, not a defect, and only a full build would discover that.
`String` / `Error` / `GoString` bodies are skipped, as `__repr__` / `__str__`
are. So are `vendor/`, `testdata/`, `_test.go`, a package literally named
`test` (Go's test convention is the filename suffix, so such a package is test
*infrastructure* — `golang-jwt/jwt`'s `test/helpers.go` is exactly that), and
anything carrying a `// Code generated … DO NOT EDIT.` line, because a learner
sent to fix generated code would be fixing the wrong file.

### The coverage map, which is the expensive part

Python hands us per-test coverage contexts for free. Go's `-coverprofile` is a
**whole-run** profile with no per-test attribution at all, so `GoAdapter` runs
the suite **once per test**, each with its own profile, and unions them.

On the vetted repo that is ~3s per test — about **two minutes** for the whole
map, tolerable only because it is cached per `(repo, commit_sha)` in the same
`.baseline_cache/` the Python baseline uses, so it happens once per commit and
never again. `BaselineFunction` gets Lambda's maximum 900s timeout for this
reason, and that ceiling is the real constraint on which Go repos can be
vetted at all.

Details that are load-bearing:

- **`-coverpkg=./...`**, or the profile only covers the package under test and
  a `request` test exercising root-package code leaves those lines looking
  untested — every mutation there misfiled as a test gap.
- Profiles name files by **import path**, not by a path relative to the repo,
  so the module path is stripped off the front. The image build refuses to
  build if `REPO_PACKAGE` disagrees with `go list -m`, because getting that
  wrong reads downstream as "this repo has no covered lines at all".
- The **green gate runs the whole suite first** rather than trusting the
  per-test runs: a test that only passes in isolation is not a green suite.
- A test that times out costs its own coverage rows, not the whole map. Lines
  only it covered then look untested and are filed as gaps — wrong, but
  conservative: a false gap is a report nobody acts on, where a false
  challenge is a bug nobody can find.

Test ids are `"<pkg dir>:<TestName>"` — `".:TestParser"`,
`"request:TestParseFromRequest"` — so an id round-trips to a package and a
`-run` anchor without a lookup table. Only `^Test` functions are collected:
benchmarks, examples and fuzz targets have no fixed pass/fail verdict.

### Running tests

`go test -v`, grouped by package so a run spanning two packages costs two
invocations rather than one per test, with an anchored `-run` alternation so
`TestParse` never also matches `TestParseUnverified`.

Results are counted from **top-level** `--- PASS/FAIL` lines only. `-v` indents
subtest results under their parent, and counting those reported 70 results for
8 requested tests — which would hand the scorer a "fraction of the suite that
went red" computed against a different denominator than the baseline's test
count. Failing subtests are reported under their parent, which is the id the
baseline knows.

A **build failure is not a test failure**: Go rejects at compile time what
Python surfaces as a red test, and that lands in the same `collection_error`
bucket Phase 3 already drops Python import errors into.

The environment is inherited unchanged. The knobs this needs — `GOCACHE` and
`GOPATH` somewhere writable, `CGO_ENABLED=0` for a pure-Go build — are
properties of where it runs, not of what it does, so the image sets them and a
developer's machine keeps its own. Forcing `CGO_ENABLED=0` in library code was
the first version, and it broke every run on a Windows host whose Application
Control policy refuses to execute the resulting binary.

### Grading, and one hole it closed

`cloud/anti_cheat.py` is per-language now. Go's rule list is shorter, and that
is a property of Go rather than a gap — with test files off limits there is
simply less to disable, and Go source has no `assert` statement. What it does
check: no added `os.Exit` / `syscall.Exit` / `runtime.Goexit` / `log.Fatal*`,
no added `t.Skip`, no added `//go:build` constraint. Still AST-based, via
`golocate -mode=fingerprint`; still a before-vs-after comparison rather than an
absolute count, because real Go code contains `log.Fatal` and build
constraints already.

The exit rule closed a real exploit: **`os.Exit(0)` added to code that runs
during the suite ends the test binary with a success status before a single
result prints, and `go test` reports `ok` over the top of it.** `fn_grade`
carries a second lock on the same hole — a run that produced no results at all
is never green, whatever its exit code.

### The front end

- `web/lib/lang.ts` is the single place the per-language rules live, mirroring
  `anti_cheat.py`. The solve screen's editability rule was
  `!path.endsWith(".py")`, which made **every file in a Go challenge read-only
  and the challenge unsolvable**.
- `web/lib/traceback-go.ts` parses `go test` output into the same `Frame[]` the
  pytest parser produces, so the spine, the gutter markers and the replay chart
  never learn which language they are showing. Go gives two shapes: a panic
  with a real goroutine stack, and a bare `t.Errorf` report with no stack at
  all. Both are handled, and the panic stack is **reversed**, because Go prints
  innermost-first and everything here assumes Python's order. GOROOT frames
  resolve to nothing and stay dimmed on the rail, exactly as site-packages
  frames do. Its tests run against output captured verbatim from a real
  mutated run, absolute temp paths and all.
- CodeMirror gets `@codemirror/lang-go`; the file tree hides `vendor/` and
  `*.test` / `*.exe` / `*.out`.
- The generation stream's location mask follows the file's extension
  (`░░░░░░.go:░░░`). A fixed `.py` on a Go repo would make every masked row
  visibly different from the unmasked ones around it, telling a learner
  exactly which rows are the challenges — the one thing masking exists to
  prevent.

### Adding a third language

Locating and splicing tokens is solved in every language; **the coverage map is
the hard one**, and Go is the evidence for how hard. Assume O(tests) suite runs
and a cache unless proven otherwise.

| | Parse & locate | Per-test coverage | Runner |
|---|---|---|---|
| **Go** — done | `go/ast` via `golocate` | one `-coverprofile` per test, unioned | `go test -run`, ids as `pkg:Name` |
| **Java** | tree-sitter | JaCoCo, one dump per test (`sessionid`), or per-class | Maven vs Gradle detection; Surefire XML |
| **C++** | tree-sitter | gcov/lcov with `__gcov_reset` between tests | CMake + CTest, `ctest -R` |

- **Java** — JaCoCo aggregates across the whole run by default, so per-test
  attribution means one dump per test. JVM startup makes the "run only covering
  tests" saving much larger than in Python, and the per-test coverage cost much
  higher.
- **C++** — the preprocessor means a located token may sit in a branch that
  never compiles for this build config, so `find_candidates` needs a
  build-config-aware skip neither Python nor Go has an equivalent of. Compile
  time, not test time, dominates: the 30s per-mutation timeout would have to
  become per-language, and the 900s baseline ceiling is already the binding
  constraint for Go.

Everything downstream of the adapter — scoring, the rejection taxonomy, test
gaps, packaging, the describer, the replay chart — is genuinely
language-agnostic and did not change for Go.

## How it was verified

Phases 6 and 7 were driven end to end in a real Chrome browser against a local
mock API serving the **real Phase 5 challenge bundles** (real tarballs, real
tracebacks), with grading that applies the submitted patch and compares against
the true source:

- opening a course, opening a challenge, the initial file opening at the right
  frame;
- clicking spine frames and walking a 12-frame trace by keyboard;
- submitting with nothing changed (blocked, and `⌘↵` did not type into the
  editor);
- a wrong edit → `FAIL` with its red tests → revert → the real fix → `PASS` →
  the reveal;
- the course then showing the challenge solved;
- drafts surviving a reload;
- the replay chart, its two lines, and the generated sentence;
- no horizontal overflow at 400px, and zero console errors throughout.

The mock stands in for AWS. **The real Lambdas have never been called from a
browser.**

Phase 8 was **not** driven in a browser: the OAuth flow leaves our origin for
github.com and back, and there is no OAuth app to leave for. It is covered by
unit tests only — signing, verification, state, the open-redirect rule, the
gate, and the one-solve award. Treat the browser half (the header control, the
redirect round trip, the cookie actually surviving the cross-site hop) as
unverified until it is deployed.

## Running it with no AWS account

The whole backend runs on this machine, open source and unbillable, from the
**same `infra/template.yaml`** the cloud path uses: **Finch** (or Docker)
builds the per-repo image, **LocalStack** serves S3, DynamoDB, Lambda, Step
Functions, Secrets Manager and API Gateway on `:4566`, and the **SAM CLI**
(`samlocal`) deploys the template into it. The web app's dev server proxies
`/api/*` to LocalStack, which is what makes the session cookie first-party and
the OAuth callback a stable `http://localhost:3100/...` URL.

The runbook is `infra/local/README.md`. Nothing in the application code is
aware of any of this: boto3 reads `AWS_ENDPOINT_URL` itself, and every ARN the
handlers use already comes from the environment.

## Deploying

1. Build and push the image for a vetted repo:
   `./infra/docker/build_and_push.sh jd__tenacity us-east-1`
2. `sam deploy` the stack with `WebOrigin` set to the Amplify URL — both the API
   and the S3 bucket's CORS rules use it, and without it the browser cannot
   download challenge trees. **`WebOrigin` must be a real origin, not `*`, for
   sign-in to work**: a credentialed cross-site cookie and a wildcard origin
   are mutually exclusive by spec.
3. In Amplify, connect the repo (`amplify.yml` is at the root) and set
   `NEXT_PUBLIC_API_URL` to the stack's `ApiUrl` output. It is inlined at build
   time; the build fails if it is missing.
4. **Sign-in, which needs two passes** — the callback URL does not exist until
   the stack does:
   a. after the first deploy, read the `OAuthCallbackUrl` output;
   b. create a GitHub OAuth app (Settings → Developer settings → OAuth Apps)
      with that exact URL as the Authorization callback URL;
   c. redeploy with `GitHubClientId`, `GitHubClientSecret` and
      `OAuthRedirectUri` set to the same URL. GitHub compares the redirect URI
      on both legs of the flow, so it is configured, never derived from the
      request — whose Host header a caller controls.
   Skipping step 4 entirely is fine: the stack deploys and every route works,
   signed out. Only submitting is refused.

## Known issues

- **Not deployed to AWS.** No AWS credentials on the build machine. The
  LocalStack path above has not been executed either: Docker Desktop's daemon
  was not running and Finch is not installed, so `docker-compose.yml`,
  `build_local.sh` and `deploy.sh` are unrun. The template they deploy does
  now pass `sam validate --lint`, which it did not before: `WebOrigin="*"`
  made the transform fail outright, and an empty `GitHubClientId` would have
  been rejected by Secrets Manager on a sign-in-disabled deploy.
- **No GitHub round trip has ever happened.** There is no OAuth app, so
  `exchange_code` and `fetch_user` — the only two functions in `cloud/auth.py`
  that touch the network — have only ever run against stubs. Everything around
  them is tested, and the full click-through (sign in → consent → back signed
  in → sign out) has been driven in Chrome against a local stand-in for
  github.com.
- **The Go image has never been built.** `infra/docker/Dockerfile.go` is
  unverified: Docker Desktop's daemon was not running on the build machine, so
  nothing in it — the pinned Go toolchain download, the `go list -m` check
  against `REPO_PACKAGE`, the warm build cache, the `golocate` build — has
  been executed. Everything it wraps was verified outside Docker, against a
  real clone of the vetted repo, on Go 1.26.5.
- **The Go build cache is a cold-start cost nobody has measured.** Lambda's
  filesystem is read-only apart from `/tmp`, so Go's cache cannot live on the
  image layer. `cloud/workspace.py` copies the image's warm cache from
  `$SEED_GOCACHE` into `/tmp` once per container. Whether that copy fits the
  2 GB ephemeral disk, and what it costs on a cold start, is unknown until the
  image is built.
- **Per-test coverage sets the ceiling on which Go repos can be vetted.**
  `golang-jwt/jwt` takes ~2 minutes for 41 tests; `BaselineFunction` has
  Lambda's maximum 900s. A Go repo whose suite is slow or large simply does
  not fit, and there is no fallback to a coarser map.
- **Go's `name_leak` penalty rarely fires.** The check splits a test id on
  `_`, which suits `test_stop_after_attempt` and not `TestStopAfterAttempt` —
  Go names are CamelCase. A Go challenge whose failing test names the mutated
  function therefore keeps a higher score than it deserves.
- **Bedrock output never seen.** Every challenge so far uses the deterministic
  template.
- **Next.js 16.3.5 export bug.** Next writes per-segment prefetch payloads to
  `out/repo/__next.repo/__PAGE__.txt` but the browser requests
  `out/repo/__next.repo.__PAGE__.txt`. On any static host every prefetch 404s.
  `web/scripts/flatten-segments.mjs` runs after `next build` and copies them to
  the expected names. Delete it once Next fixes this.
- **Chained exceptions** ("During handling of the above exception…") are not
  split by the traceback parser, so both exceptions' frames appear in one spine.
  Readable, but not ideal.
- **`pytest` at the repo root** tries to collect the extracted repos under
  `phase5_output/` and reports ~148 collection errors. Run `pytest tests`
  instead, or add a `pytest.ini` with `testpaths = tests`.
- **Replay needs the local record.** The causal path is reconstructed from the
  traceback the learner was given, which is kept in their browser. Opening a
  result link on a different device shows the reveal without the replay.

## Layout

```
bugforge/     phases 1–3: baseline, mutate, runner, select, package  (pure Python)
  languages/  the LanguageAdapter protocol + PythonAdapter (the only one)
cloud/        phase 4–5, 8 glue: handlers, anti-cheat, describer, auth, S3/DDB IO
infra/        SAM template, state machine, per-repo Docker image, vetted list
web/          phase 6–8 Next.js app (app/, components/, lib/, scripts/)
scripts/      demo/vetting CLIs for each phase
tests/        301 Python tests
amplify.yml   Amplify build config (monorepo appRoot: web)
```
