# BugForge — Master Spec
### Bharat Builds Tour · Event 01 "First Commit" · Sept 17–20, 2026 · Ship It track

---

## 0. The one-liner

**Every repo with a test suite is a debugging gym. BugForge breaks it on purpose, hands you the stack trace, and checks your fix.**

Paste a public GitHub URL. In ninety seconds you get a set of realistic bugs in that codebase, ranked by how hard they are to trace, each one verified to be catchable and solvable before a human ever sees it.

---

## 1. Idea and Impact (criterion 01)

### The problem, stated precisely

Debugging is the majority of professional engineering work and there is almost no way to practise it.

The reason isn't lack of interest — it's supply. Every debugging platform that exists has a handful of challenges, because each one requires a human to find a gnarly bug, rebuild it into a repo, write the description, and guess a difficulty. Recticode, the best example of the category, has **three challenges** and a form on its homepage asking people to please donate more. That form is the bottleneck, printed on the product.

So the real problem isn't "debugging practice doesn't exist." It's that **authoring debugging challenges is manual labour, and therefore practice barely exists at all.**

### Who it changes things for

**CS students.** Everything they practise — LeetCode, coursework — is writing new code in a file they can see entirely. Their first job is the opposite: reading unfamiliar code and finding why it misbehaves. There is currently no ladder for that skill.

**New open-source contributors.** The reason most people never make a second contribution is that understanding the codebase is harder than writing the fix. BugForge generates bugs *in the specific repo you're about to contribute to*, ordered by difficulty. That's not a challenge list, it's a generated course: **learn this codebase in ten bugs.**

**Maintainers.** Same scan, second output. Mutations that *no test catches* are holes in the test suite — that's a report maintainers actively want, and it costs nothing extra to produce (§3.5).

### The personal angle

Say this in the video, because it's true and it's specific: I contributed to Kubeflow's training operator and filed a bug where `apply.UpsertPort` silently dropped unnamed container ports, because `ptr.Equal(nil, nil)` is true. Finding it took far longer than fixing it, and nothing I had ever practised prepared me for the finding part.

---

## 2. Why this is novel

Mutation testing has existed for fifty years. Tools like `mutmut` and `cosmic-ray` make a small change to your code — flip `<` to `<=`, drop a condition, change a default — and run your tests to see whether they catch it. The purpose is grading the test suite: a mutation your tests miss is a hole in your tests.

Every mutation the tests *do* catch is counted and discarded. Nobody has ever looked at one twice.

But a caught mutation is a complete debugging challenge: a small realistic defect, a failing test, a real stack trace, and a known fix (revert it).

> **Mutation testing asks "did the tests catch this?" BugForge asks "would a human?"**
>
> Same fifty-year-old machinery, inverted objective.

That inversion is the novelty, and it's one sentence. Name mutation testing as prior art — it signals you know the field, and the inversion is more impressive when the technique is understood to be proven.

### What the inversion buys you

| Property | Why it follows |
|---|---|
| **Any repo with tests works** | No commit-history requirement. Mutate HEAD, so it always builds. |
| **Supply is unbounded per repo** | Thousands of candidate mutation sites in a mid-sized library. |
| **The answer isn't on the internet** | The bug never existed. No commit to find, no Stack Overflow thread. |
| **Grading is free and trustworthy** | The repo's own test suite is the oracle — the mutation was *selected* because that suite catches it. |
| **Ground truth is exact** | The fix is the inverse of a single-token edit. |

### The honest counter-argument, and the answer

Someone will say a flipped operator isn't a "real" bug. Have this ready: real bugs overwhelmingly *are* off-by-ones, inverted conditions, wrong defaults, and missing guards. The Kubeflow bug above was a nil-comparison. The reason mutation operators are what they are is that they were derived from studying real defects.

More importantly — **generation isn't the hard part, selection is.** Most mutations are boring. A flipped comparison that crashes on the next line is not a puzzle. The engineering is the filter that keeps only mutations whose failure surfaces far from the cause. That filter is the product (§3.4).

---

## 3. The engine — fully deterministic

**No model generates a bug, selects one, scores a difficulty, or decides a verdict.** The only model call in the entire system is §3.6, and it has a deterministic fallback.

### 3.1 Baseline (once per repo)

Clone at a pinned SHA. Run the full suite with coverage contexts:

```
pytest --cov=<pkg> --cov-context=test
```

This produces three things you need:

1. **Green baseline** — if HEAD isn't green, reject the repo. You can't attribute a later failure to your mutation otherwise.
2. **A line → tests map.** Coverage contexts record *which test* executed *which line*. This is the key performance mechanism (§3.3).
3. **Total test count**, for the signal-to-noise metric.

Cache this per repo. It's the expensive operation and you do it once.

### 3.2 Mutation generation — AST located, surgically applied

Parse each source file with Python's `ast` module and find candidate nodes. Operators:

| # | Operator | Example |
|---|---|---|
| 1 | Comparison swap | `<` → `<=`, `>` → `>=`, `==` → `!=` |
| 2 | Boundary shift | `range(n)` → `range(n-1)`, `x[1:]` → `x[2:]` |
| 3 | Arithmetic swap | `+` → `-`, `*` → `//` |
| 4 | Boolean swap | `and` → `or` |
| 5 | Negation drop | `if not x:` → `if x:` |
| 6 | Return mutation | `return True` → `return False`, `return x` → `return None` |
| 7 | Default argument | `def f(retries=3)` → `def f(retries=4)` |
| 8 | Augmented assign swap | `+=` → `-=` |
| 9 | Raise removal | delete a `raise` inside an `except` |
| 10 | Break/continue swap | `break` → `continue` |

**The critical implementation detail:** do **not** mutate the AST and call `ast.unparse`. That discards comments and reformats the entire file, which both destroys readability and makes the mutation obvious by diff.

Instead, use the AST purely to **locate** the token, then do a surgical text edit on that one line. For `ast.Compare`, the operator token sits between `node.left.end_col_offset` and `node.comparators[0].col_offset` — slice that span out of the source line, swap the token, splice it back. The file is byte-identical everywhere else.

This is the difference between a working engine and a broken one. Get it right on Thursday.

**Only mutate covered lines.** A mutation on an uncovered line can never be caught by any test — it's automatically a test gap, not a challenge. Filtering to covered lines up front removes most of your wasted work.

### 3.3 Execution — coverage-guided, the performance trick

Running the full suite per mutation is brutal: 60 mutations × 30s = half an hour per repo, and it scales badly.

Use the line→tests map from §3.1. **For a mutation on line L, run only the tests that execute line L.** That's typically 2–20 tests instead of 2,800 — a 10 to 100× speedup, and it's exactly correct: no other test can possibly observe the change.

Run a full suite pass only on the handful of candidates that survive selection, to get accurate signal-to-noise and a clean traceback.

**Cost note for the Ship It score:** batch 10–20 mutations per Lambda invocation so the clone and dependency load amortise across them, instead of one invocation per mutation.

### 3.4 Selection — where the engineering is

Classify each candidate by outcome:

| Outcome | Disposition |
|---|---|
| Import/collection error | **Drop** — no puzzle, just a wall |
| 0 tests fail | **→ test-gap report** (§3.5), not a challenge |
| More than 25% of the suite fails | **Drop** — too loud |
| Timeout | **Drop** — probably an infinite loop |
| 1 to k tests fail | **Candidate challenge** → score it |

Then score, all deterministic:

**Displacement** — how far the symptom is from the cause. Parse the failing test's traceback:
- deepest frame is in the mutated file → `0`
- mutated file appears `k` frames up → `k`
- mutated file does not appear in the traceback at all → `4` (capped). This is the *best* case: the mutation corrupted state that blew up somewhere else entirely.

**Search space** — distinct source files executed by the failing test, from coverage. Proxy for how much code must be read.

**Noise** — failing tests / total tests. One red out of 300 is far harder to chase than forty.

**Name leak** — does the failing test's name share a token with the mutated function's name? (`test_retry_backoff` failing on a mutation in `retry_backoff` gives the answer away.) Boolean penalty.

```
d = min(displacement, 4) / 4
s = min(search_space, 20) / 20
n = 1 - min(failing / total * 40, 1)

score = clamp(1, 10,  1 + 9*(0.45*d + 0.25*s + 0.30*n) - 1.0*name_leak )
```

Admit only `score >= 3.0`. Report how many candidates died at each stage — that rejection table is one of your best slides, because it proves there is judgement in the pipeline rather than a firehose.

### 3.5 Second output — the maintainer report

Mutations where **no test failed** are precisely what mutation testing was invented to find: places the test suite has a blind spot. You already computed them.

```
bugforge gaps github.com/you/yourrepo
```

One scan, two products. Challenges for contributors, gaps for maintainers. This is what gives the project a reason to spread beyond single-player practice.

### 3.6 The only model call in the system

The pipeline has already computed, deterministically:

- the failing test's name (`test_retry_stops_after_max_attempts`)
- the assertion delta (expected ≤ 3, got 7)
- the affected module's docstring
- the exception type, if any

Bedrock's entire job is to turn those facts into a challenge **name** and a **two-sentence symptom description in a user's voice**. `("Retry Storm", "A failed request keeps retrying instead of giving up after three attempts. Throughput collapses under load.")`

Hard constraints, enforced in code:
- The output is post-checked against every file path and function name in the mutation. If any leaks, regenerate once, then fall back.
- **Deterministic template fallback** ships from the first line of code: `"{test_name} expected {expected}, got {actual}."` The entire platform works with Bedrock switched off.

That is the whole AI footprint. Say so explicitly on camera: *"the model writes two sentences of prose. It does not choose the bug, rank the bug, or decide whether you fixed it. Everything that matters is a test run."*

### 3.7 Grading — no hidden tests needed

The repo's own suite is the oracle, because the mutation was selected precisely because that suite catches it. This is much simpler than it sounds and much more trustworthy than anything hand-written.

1. **Patch hygiene check** (AST, not regex): reject if the patch modifies any test file, deletes an `assert`, adds a bare `except: pass`, adds `@pytest.mark.skip`, or calls `sys.exit`.
2. Apply the patch to the broken tree.
3. Run the full suite.
4. Green → **PASS**. Red → **FAIL**, with the still-failing test names.

A learner who fixes it differently from the original also passes, and that's correct.

**Honest limitation:** the repo is public, so a determined learner can diff against upstream. Recticode has the identical property — their challenge repos are on GitHub. Mitigate by stripping README and package metadata in practice mode and revealing the repo only after submission, and treat the leaderboard as honour-based, as every platform in this category does. Name this in the writeup rather than hoping nobody asks.

---

## 4. Built on AWS (criterion 02) — Ship It

Generation is a **batch job**. Nothing generative runs when a judge clicks anything.

```
   public GitHub repo URL
          │
          ▼  git clone --filter=blob:none  (zero API calls after this)
 ┌──────────────────┐
 │  Step Functions  │  forge_repo
 │                  │
 │  1. Lambda: baseline — full suite + coverage contexts
 │  2. Lambda: generate AST mutation candidates (covered lines only)
 │  3. Map state: batches of 15 mutations, concurrency 10
 │       └─ Lambda (ECR image): apply, run covering tests, classify
 │  4. Lambda: score survivors, full-suite pass, capture traceback
 │  5. Lambda: Bedrock naming (1 call/challenge, template fallback)
 │  6. Lambda: write challenges + gap report
 └──────────────────┘
          │
     ┌────┴─────┐
     ▼          ▼
 DynamoDB      S3
 challenges    /public/  broken tree tarball, traceback
 submissions   /answers/ mutation patch  ← separate IAM, never presigned
 gap reports
 leaderboard

   Amplify (Next.js) ─▶ API Gateway ─▶ Lambda
                                        ├ list/get challenge  (DDB read)
                                        ├ presign public tree
                                        ├ grade submission    (ECR image)
                                        └ forge request       (start SFN)
   Cognito ── GitHub login
   EventBridge ── nightly re-forge of tracked repos
```

**Every service justified, none for the checklist:**

| Service | Why it's genuinely needed |
|---|---|
| **Lambda + ECR** | Untrusted code execution. Firecracker microVM per invocation, dependencies pre-baked into the image, no VPC, IAM scoped to one S3 prefix, 60s timeout. Security answer and scale-to-zero answer in one. |
| **Step Functions** | Fan-out over mutation batches with per-item failure tolerance. Most candidates legitimately fail — set a high `ToleratedFailurePercentage` and keep survivors. This is what Map states are for. |
| **DynamoDB** | On-demand. Challenges, submissions, gap reports, leaderboard. |
| **S3** | Two prefixes, **separate IAM principals**. The answers prefix is never presigned to a browser. |
| **API Gateway** | REST surface. |
| **Amplify Hosting** | Next.js frontend, the live URL. |
| **Cognito** | GitHub login, progress tracking. |
| **Bedrock** | Two sentences of prose per challenge, with a template fallback. |
| **EventBridge** | Scheduled re-forge so tracked repos stay fresh against new commits. |

**Cost decisions, which are explicitly part of the Ship It score — say these on camera:**

- Serving a challenge is one DynamoDB read plus a presigned URL. Idle cost is effectively zero.
- Coverage-guided test selection cuts compute per mutation by 10–100×.
- Mutations are batched 15 per invocation so the clone and dependency load amortise.
- Bedrock is one short call per *admitted* challenge, not per candidate — roughly a 1-in-8 ratio after filtering.
- Grading is one short Lambda invocation with no idle capacity.

**Security, also worth saying on camera:** the learner's patch executes in a Lambda with no network, no credentials, and a hard timeout. You are running untrusted code from the internet and you have an actual answer for it.

---

## 5. Execution (criterion 04) — the four-day plan

Each day ends with something shippable. You are never in a state where failure means nothing to submit.

### Thursday — the engine, on your laptop. No AWS, no UI.

1. Pick and vet 6 candidate repos (§9).
2. Baseline runner: full suite + coverage contexts, line→tests map.
3. AST mutation generator with operators 1–7, **surgical text edit, not unparse**.
4. Coverage-guided execution and classification.
5. Scoring.

**Checkpoint:** one repo, one admitted challenge, real captured traceback, printed rejection taxonomy.
**Kill criterion:** if the surgical edit is still mangling files at midnight, cut to operators 1 and 7 only (comparison swap and default argument) — both are single-token edits and both produce excellent bugs.

### Friday — on AWS.

ECR image, the verify Lambda, the Step Functions forge machine, DynamoDB writes, Bedrock naming with fallback.

**Checkpoint:** a bank of 30+ challenges across 3 repos, forged in the cloud.
**Kill criterion:** if dependency baking into the Lambda image is fighting you by Friday afternoon, move execution to AWS Fargate or CodeBuild and keep everything else. Don't lose Saturday to packaging.

### Saturday — the web app.

Landing with a working forge box, repo browse, challenge cards, trace view, submit, grade, result, leaderboard.

**Checkpoint:** solve a challenge end to end in a browser at a public URL.

### Sunday — freeze at noon.

No new features after 12:00. Record the video against the pre-built bank so nothing generative runs live. Writeup, blog post, Builder Center profile.

**If you have a team of 2–4:** one person owns the engine (Thu–Fri), one owns AWS infra (Fri), one owns frontend (starts Thursday against fixture JSON, doesn't wait for the backend), one owns the video and writeup from Saturday morning. The video is a full day's work and treating it as a Sunday afterthought is how good projects lose.

---

## 6. Frontend (Best UI track)

Recticode's aesthetic is right — dark, terminal, monospace. Their weakness is that it's static: a traceback is a block of text.

### Design language

- Base `#0A0B0D`, panel `#111316`, hairline border `#1E2126`
- Text `#C9CDD3`, dim `#6B7280`, error amber `#E8A33D`, success `#4ADE80`, causal path violet `#8B5CF6`
- JetBrains Mono everywhere, headings included. No sans-serif anywhere.
- Square corners, 1px borders, no shadows, no gradients, no rounded cards, no glass.
- Motion: blinking block cursor, 120ms fades. Nothing bounces.

In a field of two thousand projects with purple gradients and rounded cards, the one that looks like a debugger will be the only one that looks deliberate. **The restraint is the design.**

### Screen 1 — Landing. The input field is the hero.

> # Every repo is a debugging gym.
> Paste any public repo with a test suite. BugForge breaks it the way it would break in production, hands you the stack trace, and checks your fix. Nothing here was written by hand.

```
┌────────────────────────────────────────┬──────────────┐
│ github.com/                            │  forge bugs  │
└────────────────────────────────────────┴──────────────┘
     try: pallets/click · jd/tenacity · arrow-py/arrow
```

Below it, the generation stream as live terminal output — **including the rejections**:

```
$ bugforge forge github.com/pallets/click

  cloning pallets/click @ 8fc5f8e ................. ok
  baseline suite .................................. 2,847 passed
  coverage map .................................... 41,203 lines
  generating candidates ........................... 60
  running ......................................... ████████░░ 71%

  ✓ core.py:412       1 test red    displacement 3   KEEP
  ✗ types.py:88      41 tests red   too loud         drop
  ✗ parser.py:203     0 tests red   test gap → report
  ✓ decorators.py:71  2 tests red   displacement 4   KEEP

  9 challenges ready · 3 test gaps found
```

Anyone can claim generation. Showing what you threw away and why is what proves there's a filter.

### Screen 2 — Browse by repo

```
pallets/click       9 challenges   ▊▊▊░░░░░░░  avg 4.2
jd/tenacity        11 challenges   ▊▊▊▊▊▊▊░░░  avg 7.1
arrow-py/arrow     14 challenges   ▊▊▊▊▊░░░░░  avg 5.8
```

Not a flat list — a generated course per repo, ordered by difficulty.

### Screen 3 — Challenge cards

```
MEDIUM  python              HARD  python                EASY  python
Ghost Session               Retry Storm                 Off Switch
Users get logged out at     A failed request retries    The feature flag is
random, but only under      forever instead of backing  checked, but the
load.                       off after three attempts.   feature runs anyway.
pallets/click               jd/tenacity                 arrow-py/arrow
▊▊▊░ ▊▊░░ ▊▊▊▊              ▊▊▊▊ ▊▊▊░ ▊▊░░              ▊░░░ ▊░░░ ▊▊▊▊
```

Names, not IDs. Symptom-voice descriptions, never naming a file. Difficulty as three measured bars — displacement, search space, noise — with a tooltip on each. Where Recticode lets a submitter type "7", you show your work.

### Screen 4 — Solve view

Three panes. **Left: the traceback as a clickable spine** — each frame is a row, clicking opens that file at that line, visited frames get a filled marker. This is the clearest win over Recticode: a wall of text becomes navigable structure, and it's a small build.

Center: CodeMirror 6, dark, failing line marked in the gutter, tabs accumulating as files open. Right: description, failing test names, monospace timer, file tree. Bottom: terminal status bar — `click/retry-storm · 04:31 · 3 files open · ⌘↵ submit`, with keybindings that actually work.

### Screen 5 — Result

`PASS — 2,847 tests green` in terminal style. Then the actual mutation diff: one token, highlighted. Seeing that the whole thing hinged on `<` becoming `<=` is the moment the product lands.

### Stack

Next.js App Router on Amplify, Tailwind, CodeMirror 6 (lighter than Monaco). No component library — the design is borders and monospace and shadcn defaults will fight it.

---

## 7. Learning (criterion 03)

This is scored and it wants specifics. Write the honest version:

- First production AWS deployment under a deadline.
- First Step Functions Map state — and specifically, learning that `ToleratedFailurePercentage` exists, because in this pipeline most branches are *supposed* to fail.
- First Lambda container images from ECR, after discovering that runtime dependency installation is unworkable.
- First time writing an IAM policy whose job is to make "this function can do nothing except read one object and write one object" actually true, in order to safely run untrusted code.
- The `ast.unparse` discovery: the obvious approach destroys the file, and the fix was to use the AST only for locating and do surgical text edits.

Name what fought back. That reads as real and it scores.

---

## 8. Demo video (criterion 05) — 3 minutes

This is the only thing judges see. Budget a full day.

| Time | Content |
|---|---|
| 0:00–0:15 | A real stack trace on screen. "The file this points at is not the file that's broken. Nothing in a CS degree prepares you for that." |
| 0:15–0:40 | Every debugging platform has three challenges, because a human writes each one. Show Recticode's donate-a-bug form. |
| 0:40–1:15 | Paste a URL. Watch it forge — **including the rejects scrolling past**. Nine challenges from a repo nobody prepared. |
| 1:15–1:55 | Solve one. Click through the trace spine, find it three files upstream, submit, 2,847 tests green. Reveal the diff: one token. |
| 1:55–2:20 | Architecture. Step Functions Map fan-out, Lambda microVMs running untrusted code, coverage-guided selection. **"The model writes two sentences. It doesn't pick the bug, rank it, or grade you."** |
| 2:20–2:40 | The second output: `bugforge gaps` — the same scan tells maintainers where their tests are blind. |
| 2:40–3:00 | Learn-this-codebase-in-ten-bugs, and the live URL. |

Every frame is from the pre-built bank. Nothing generative runs during recording.

---

## 9. Demo repos — vet these Thursday morning

Need: pure Python, pytest, fast suite, no network or database, permissive licence, readable, under ~20k LOC.

Strong candidates to check first: **`pallets/click`** (BSD, fast, excellent tests), **`jd/tenacity`** (Apache-2.0, small, and thematically perfect — a retry library produces wonderful "retries forever" bugs), **`arrow-py/arrow`** (Apache-2.0, date handling, boundary bugs everywhere), **`theskumar/python-dotenv`** (BSD, tiny, good for fast iteration), **`python-jsonschema/jsonschema`** (MIT), **`Textualize/rich`** (MIT).

Verify each yourself — clone it, run `pytest`, time it. Don't take the list on faith. Pick three for the demo bank and keep the rest as backup.

Licence hygiene: keep only MIT / Apache-2.0 / BSD / ISC, never strip `LICENSE` when packaging the tree, and show repo + licence + commit SHA on every challenge. One line about this in the writeup; almost nobody will have it.

---

## 10. Blog post (separately awarded — top 5, Logitech keyboards)

Write the one nobody else will:

> **"We generated 600 bugs and threw away 540 of them. Here's what the other 540 were."**

The rejection taxonomy is more interesting than the success story and your pipeline produces it for free. It's also the single best evidence that selection — not generation — is the real work. Draft Saturday night, publish on AWS Builder Center Sunday morning, link it in the submission.

---

## 11. Risks, named

| Risk | Mitigation |
|---|---|
| Surgical text edit mangles files | Solve Thursday. Fall back to single-token operators only. |
| Test suite too slow | Coverage-guided selection. Pick fast repos. |
| Dependency baking into Lambda image | Do it first on Friday. Fargate/CodeBuild as escape hatch. |
| Yield too low on a repo | Vet six repos Thursday; hardcode the three best. |
| Learner diffs against upstream GitHub | Strip metadata in practice mode, reveal repo after submit, honour-based leaderboard. Recticode has the same property. |
| Answer leakage via S3 | Separate prefix, separate IAM, per-object presigning only. |
| Untrusted execution | Lambda microVMs, no network, no credentials, 60s ceiling. |
| Bedrock unavailable | Deterministic template fallback ships from day one. |

---

## 12. Copy discipline

- Never say "AI-generated bugs." Say "generated." The pipeline is deterministic mutation plus execution; the word AI invites an assumption of hallucination that doesn't apply.
- Never say "any repo" unqualified. Say "any repo with a test suite." Still an enormous universe, and the qualifier makes you sound like you've actually run it.
- Every description leads with the symptom. Never the cause.
- Credit Recticode and mutation testing openly. Prior art you cite is credibility; prior art someone finds is a hole.