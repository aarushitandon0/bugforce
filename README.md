# BugForge

**Practice debugging on real open source code, with bugs nobody wrote by hand.**

BugForge takes a real repository, breaks it in one place, proves the break is
catchable by that repository's own test suite, and hands you the broken tree
with a failing test. You find the bug and patch it. The repository's real test
suite decides whether you were right. No model grades you, and there is no
hidden answer key in the grading path.

Track: **Build It.** The whole backend runs on one machine with no AWS account,
no credentials and nothing billable.

---

## Why this exists

Every practice platform drills the same thing: given a blank editor and a
problem statement, write a function. That is the part of the job that a junior
engineer does least.

The actual work, and the part interviews increasingly test, is the opposite
shape. You are dropped into a codebase you did not write, something is broken,
a stack trace points at a line that is not the problem, and you have to reason
backwards from a symptom to a cause. Debugging is the skill, and there is
almost nowhere to practise it deliberately, because building the exercise
requires a broken codebase that is broken in an interesting and verifiable way.

BugForge generates those. The key insight is that a mutation is only a good
exercise if the repository's own tests catch it. That single filter gives you:

- **A ground truth.** The bug is fixed when the suite is green. Nothing else
  needs to judge it.
- **A difficulty signal.** How far the failing test sits from the broken line,
  how many files the test touches, and how loudly it fails are all measurable
  before a human ever sees the challenge.
- **A byproduct that is arguably more valuable than the challenge.** A mutation
  the suite does *not* catch is a hole in that repository's test coverage. Those
  are collected and reported as test gaps.

That last point is worth stating plainly: the same pipeline that makes practice
problems also audits the test suite of any repository you point it at.

---

## How it works

### The pipeline

Eight stages, orchestrated by AWS Step Functions, all running against one
container image that has the target repository and its full test dependencies
baked in at build time.

```
Baseline -> Generate -> AnyCandidates -> RunBatches -> Score -> Describe -> Persist
```

1. **Baseline.** Run the suite once under coverage. This produces a
   line-to-tests map: for every executable line, which tests actually execute
   it. This map is the spine of everything downstream. A repository whose suite
   is not green here is rejected, because a red baseline has no usable signal.

2. **Generate.** Walk the AST and find mutation sites. Eight operators:
   `RETURN`, `BOUNDARY`, `COMPARISON`, `NEGATION`, `BOOLEAN`, `ARITHMETIC`,
   `DEFAULT_ARG`, `TYPE_CHECKING`. Each flips exactly one token, for example
   `return self` to `return None`, or `<=` to `<`. Only lines the baseline
   proved are covered become candidates, so no effort is wasted on code no test
   reaches. Candidates are cut into batches.

3. **RunBatches.** A Step Functions Distributed Map fans the batches out. Each
   worker applies one mutation to a scratch copy of the tree and runs only the
   tests the baseline says cover that line. This targeted run is what makes the
   whole thing affordable: a full suite per mutation would be unusable.

4. **Score and classify.** Every mutation lands in one of six outcomes:

   | outcome | meaning |
   |---|---|
   | `ADMITTED` | the suite caught it, and it scored well enough to be a challenge |
   | `TEST_GAP` | the covering tests ran and stayed green: a real coverage hole |
   | `DROP_too_loud` | it broke so much of the suite that the trace gives it away |
   | `DROP_low_score` | catchable, but too easy to be worth solving |
   | `DROP_timeout` | it caused a hang |
   | `DROP_catastrophic` | the tree stopped importing |

5. **Describe.** Generate a title and a one-line symptom description from the
   failure, for example "test_wait_arbitrary_sum raised TypeError". The
   description deliberately names the symptom, never the cause.

6. **Persist.** Package the broken tree, write the challenge rows, and publish
   the test gap report.

### Difficulty is measured, not guessed

Each admitted challenge gets a score from 1 to 10 built out of three measured
quantities, in `bugforge/select.py`:

```
d = min(displacement, 4) / 4        # stack frames between the failure and the bug
s = min(search_space, 20) / 20      # source files the failing test executes
n = 1 - min(noise * 40, 1)          # inverse of what fraction of the suite went red

score = 1 + 9 * (0.45*d + 0.25*s + 0.30*n) - name_leak
```

**Displacement** is weighted highest because it is the thing being trained. A
bug whose traceback points straight at it is a typo hunt. A bug four frames
above where the exception surfaced is a real investigation. The bundled
tenacity challenge is a good example: the `TypeError` is raised at `wait.py:107`
and the mutation is at `wait.py:51`.

**Noise** is inverted on purpose. If one mutation turns half the suite red, the
intersection of the failures points at the cause immediately. A single quiet
failure gives you far less to triangulate from, so it scores higher.

**`name_leak`** is a penalty, not a bonus. If the failing test is called
`test_radd` and the broken function is `__radd__`, the name has given away the
answer, so the score is pulled down.

Difficulty bands (easy, medium, hard) are then cut from each repository's own
distribution rather than at fixed thresholds. Fixed cuts at 5 and 7 put 51 of
tenacity's 56 bugs into "medium", which makes the label carry no information. A
6.5 is a hard bug in a shallow codebase and an easy one in a deep one.

### Grading

`cloud/handlers/fn_grade.py`. No model, no hidden tests, no heuristics:

1. **Patch hygiene, via the AST.** Test files are rejected outright, so you
   cannot delete the failing test. The applied result is then diffed against the
   original tree to reject changes that neutralise the suite rather than fix the
   bug.
2. **Apply** the patch to a clean extraction of the broken tree.
3. **Run the full suite**, not just the failing test, so a fix that breaks
   something else fails.
4. **Verdict.** All green is `PASS`. Otherwise `FAIL` with the names of the
   tests still red.

The grading function has no IAM permission to read the `answers/` prefix at
all. It does not need one: the mutation was selected precisely because the
suite catches it, so a green suite *is* the proof. A separate function with a
different role serves the answer reveal, and only for a submission that has
already passed.

### Anti-spoiler design

The answer exists in exactly one place, an S3 object under `answers/`, and no
browser is ever handed a URL for it. Specifically:

- Answer-bearing fields (the patch, the mutated line, the operator) are
  deliberately kept out of the challenges table, so the API cannot leak them
  even by accident.
- The two S3 prefixes, `public/` and `answers/`, carry different IAM policies.
- The live forge stream masks file paths, because a learner who can read the
  location off the stream has already solved the challenge.

---

## Tech stack

Everything here is from the **Build It** column: open source, local, no account.

| Category | Tool | What it does here |
|---|---|---|
| Serverless | **LocalStack** | S3, DynamoDB, Lambda, Step Functions, Secrets Manager, IAM and CloudFormation on `:4566` |
| Serverless | **AWS SAM CLI** | deploys `infra/template.yaml` into LocalStack, and serves the HTTP routes LocalStack community cannot |
| Containers | **Docker** (Finch-compatible) | builds the one image per vetted repo, repo and test dependencies baked in |
| Orchestration | **Step Functions** | the eight-stage forge workflow, including a Distributed Map for the batch fan-out |
| Data | **DynamoDB** | challenges, test gaps, submissions, progress, leaderboard |
| Data | **S3** | the two prefixes, public trees and sealed answers |
| Auth | **Secrets Manager** | session signing key and GitHub OAuth credentials |
| Runtime | **Python 3.12** on Lambda container images | the whole pipeline |
| Web | **Next.js 16**, React 19, Tailwind 4, CodeMirror 6 | the app, including the in-browser editor and traceback walker |
| Tests | **pytest** and **vitest** | 369 Python tests, 126 web tests |

The same `infra/template.yaml` describes both the local and the deployed stack.
There is one description of the system, not two. `infra/local/localize.py`
generates a LocalStack-compatible copy rather than forking the template.

---

## Running it

Full detail, including every failure mode and why it happens, is in
[`infra/local/README.md`](infra/local/README.md). The short version:

```bash
pip install -r requirements.txt -r requirements-local.txt

# 1. the backend
docker compose -f infra/local/docker-compose.yml up -d

# 2. the image for one vetted repo (slow the first time, on purpose)
./infra/docker/build_local.sh jd__tenacity

# 3. the stack
./infra/local/deploy.sh jd__tenacity

# 4. the challenges
python infra/local/seed.py phase5_output

# 5. the HTTP API, left running
./infra/local/start_api.sh jd__tenacity

# 6. the web app, in another terminal
cd web && BUGFORGE_LOCAL_API=http://127.0.0.1:3101 npm run dev -- --port 3100
```

Then open `http://localhost:3100`.

### What is real and what is a local stand-in

This matters for anyone evaluating the project, so it is stated plainly rather
than buried.

**Real, running locally:** the full eight-stage pipeline code, the scoring, the
grader, the anti-cheat rules, all sixteen API routes, the Step Functions
definition, the IAM split between the two S3 prefixes, and every screen.

**Substituted locally, with the reason:**

- **Forging a new repository** needs a container-image Lambda, and LocalStack
  community refuses to start one ("Container images are a Pro feature"). The
  pipeline code is unchanged and correct; it cannot be *invoked* by LocalStack's
  Lambda. Running it needs LocalStack Pro or a real AWS deploy.
- **The bundled challenges** were therefore produced by running the real
  pipeline offline. `infra/local/seed.py` loads that output into the local stack,
  writing exactly what `fn_persist` writes: the same table rows, the same S3
  keys. It substitutes for the runtime, not for the pipeline.
- **Grading** runs in the API process locally instead of being invoked as a
  separate Lambda, for the same LocalStack reason. It is the same
  `fn_grade.handler`, in the same image, producing the same verdicts.
- **Sign-in** uses a fixed local user so the app is usable without registering a
  GitHub OAuth app. Real GitHub OAuth is implemented and documented.

Both local switches are ignored unless `AWS_ENDPOINT_URL` is set, so a real
deployment cannot honour them even if a flag leaks into its environment.

---

## Repository layout

```
bugforge/           the pipeline library: baseline, mutate, select, package
  languages/        per-language adapters (Python, Go)
cloud/              Lambda handlers and shared AWS helpers
  handlers/         one file per function, ten in total
  anti_cheat.py     AST-level patch hygiene rules
infra/
  template.yaml     the single SAM template, local and deployed
  statemachine/     the Step Functions definition
  docker/           the per-repo image build and the vetted repo list
  local/            LocalStack orchestration, and its own detailed README
web/                the Next.js app
  lib/              logic, each file with its own test
  components/       screens and UI
tests/              369 pytest tests
```

---

## Current scope

One vetted repository is forged and bundled: **jd/tenacity**, at a pinned
commit, Apache-2.0. From it, **56 challenges** across easy, medium and hard,
and **44 test gaps** in tenacity's own suite.

A stack carries one image and an image carries one repository, so exactly one
repository is forgeable per stack. A second repository means a second image and
a second stack. Go is supported by a second language adapter and a second
Dockerfile, and the vetted list pins a commit per repository so that line
numbers cannot drift under a moving branch.
