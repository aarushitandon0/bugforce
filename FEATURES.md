# BugForge — what is built, phase by phase

BugForge turns any vetted open-source Python repo into debugging practice. It
mutates one token of real source, keeps the mutation only if the repo's **own**
test suite catches it, and hands you the broken tree plus the stack trace. Every
mutation the suite *misses* becomes a test-gap report for the maintainers.

No model picks, scores, or grades anything. There is exactly one model call in
the whole system, and it only writes a title and two sentences of prose.

**Status:** Phases 1–7 are code-complete and tested locally. **Nothing is
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

Tests: **190 Python tests**, **73 web tests**, all passing.

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

- **22 resources**: 9 Lambda functions, 4 DynamoDB tables (challenges,
  submissions, gap reports, leaderboard), 1 S3 bucket, 1 HTTP API, 1 state
  machine, and a **separate IAM role per function**.
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
  `pip install`ed at runtime. Currently vetted: `jd/tenacity` @ `3e58094d`.
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
`GET /gaps`.

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

## Deploying

1. Build and push the image for a vetted repo:
   `./infra/docker/build_and_push.sh jd__tenacity us-east-1`
2. `sam deploy` the stack with `WebOrigin` set to the Amplify URL — both the API
   and the S3 bucket's CORS rules use it, and without it the browser cannot
   download challenge trees.
3. In Amplify, connect the repo (`amplify.yml` is at the root) and set
   `NEXT_PUBLIC_API_URL` to the stack's `ApiUrl` output. It is inlined at build
   time; the build fails if it is missing.

## Known issues

- **Not deployed.** No AWS credentials on the build machine.
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
cloud/        phase 4–5 glue: handlers, anti-cheat, describer, S3/DDB IO
infra/        SAM template, state machine, per-repo Docker image, vetted list
web/          phase 6–7 Next.js app (app/, components/, lib/, scripts/)
scripts/      demo/vetting CLIs for each phase
tests/        190 Python tests
amplify.yml   Amplify build config (monorepo appRoot: web)
```
