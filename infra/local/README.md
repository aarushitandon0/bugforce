# Running BugForge with no AWS account

This is the **Build It** path: the whole backend on this machine, open source,
nothing billable and no credentials. It uses the same `infra/template.yaml`
the cloud path uses -- the seven pipeline lambdas, the Step Functions
workflow, the two S3 prefixes with different IAM -- so there is one
description of the system, not two.

| piece | tool | what it does here |
|---|---|---|
| containers | **Finch** (or Docker) | builds the one image per vetted repo |
| serverless runtime | **LocalStack** | S3, DynamoDB, Lambda, Step Functions, Secrets Manager, API Gateway, IAM, CloudFormation on `:4566` |
| deployment | **SAM CLI** (`samlocal`) | deploys `infra/template.yaml` into LocalStack |
| HTTP API | **SAM CLI** (`sam local start-api`) | serves the routes LocalStack community cannot |
| web | Next.js dev server | proxies `/api/*` to the API so the session cookie is first-party |

## Once, up front

```bash
pip install -r requirements.txt -r requirements-local.txt
```

`requirements-local.txt` brings `samlocal` and `awslocal`. You also need the
SAM CLI itself and a container runtime. For Finch:

```bash
finch vm init      # first time only
finch vm start
export BUGFORGE_CONTAINER=finch
```

## Every time

```bash
# 1. the backend
docker compose -f infra/local/docker-compose.yml up -d     # or: finch compose ...

# 2. the image for one vetted repo (slow the first time: it clones the repo
#    and installs its test dependencies at build time, on purpose)
./infra/docker/build_local.sh jd__tenacity

# 3. the stack
./infra/local/deploy.sh jd__tenacity

# 4. the challenges, from a recorded run -- the forge itself cannot run here,
#    see "What LocalStack community cannot run" below
python infra/local/seed.py phase5_output

# 5. the HTTP API -- a separate process, leave it running
./infra/local/start_api.sh jd__tenacity

# 6. the web app, in a third terminal
cd web
BUGFORGE_LOCAL_API=http://127.0.0.1:3101 npm run dev -- --port 3100
```

Skip step 4 and every screen loads, empty: no repos, no challenges, no gaps.
That is the pipeline not having run, not the API being down.

## What LocalStack community cannot run

Every pipeline lambda is a **container-image** function -- the target repo and
its test suite are baked into the image, which is the whole point of the build
in step 2 -- and LocalStack community will not start one:

```
Could not start new environment: NotImplementedError:
Container images are a Pro feature.
```

So `POST /forge` succeeds, Step Functions starts, and the first state fails
three retries deep with `Lambda.ServiceException`. Nothing is written, and the
symptom is an empty `/repos` and an empty `/gaps` rather than an error.
Grading a submission fails the same way: `fn_grade` is invoked in LocalStack
too.

The two HTTP functions are unaffected. `sam local start-api` runs them on this
machine's Docker rather than inside LocalStack, which is why every read-only
screen works.

`seed.py` closes the gap for browsing: it writes exactly what `fn_persist`
writes -- the same DynamoDB items, the same `public/` and `answers/` keys --
from a completed run's output directory. The challenges, scores, tracebacks
and trees are real pipeline output, produced offline. It substitutes for the
runtime, not for the pipeline. Forging a *new* repo here needs LocalStack Pro
or a real AWS deploy.

## Why the API is its own process

`AWS::ApiGatewayV2::Api` -- the HTTP API every route hangs off -- is the one
resource LocalStack **community** does not implement. It is a Pro feature. The
deploy does not fail; CloudFormation creates a stub and the stack's `ApiUrl`
output comes back as `unknown.execute-api`, with nothing behind it. Ignore
that output. S3, DynamoDB, Secrets Manager, IAM and Step Functions are served
normally; the Lambdas are not, because they are container images -- see "What
LocalStack community cannot run" above.

`sam local start-api` serves those routes from the **same template**, turning
each function's `HttpApi` events into real ones and invoking the function in a
container per request. Everything underneath stays in LocalStack, reached over
its docker network -- including the eight pipeline lambdas Step Functions
invokes, which are never served here.

Three details are load-bearing:

- The two served functions are told where LocalStack is by an
  `AWS_ENDPOINT_URL` **declared in a generated copy of the template**
  (`localize.py`). It cannot come from `--env-vars`, which SAM applies only to
  names the template already declares, nor from `--container-env-vars`, which
  is honoured only in a debugging session. Both were tried; neither reached
  the container. Without it the calls leave for real AWS and come back
  `UnrecognizedClientException`, which reads like a credentials problem and is
  really a routing one.
- Their remaining environment is read back off the **deployed** functions
  rather than restated (`env_vars.py`), so a table renamed in the template
  needs no edit here and the local API cannot drift from the local stack.
- A presigned URL is built from the signing client's endpoint, so the one
  above would hand the browser `http://bugforge-localstack:4566/...`, a name
  only containers can resolve. `S3_PUBLIC_ENDPOINT_URL`, declared alongside it
  by `localize.py`, gives `cloud/s3_io.py` a second client on `localhost:4566`
  used for presigning only. Without it the solve screen's tree download fails
  at DNS, which the web app reports as a network error.

The browser reaches the API at `/api` on the web app's own origin, which the
dev server proxies -- see `web/next.config.mjs` for why sign-in needs that.
`next.config.mjs` sets `NEXT_PUBLIC_API_URL` to `/api` itself whenever
`BUGFORGE_LOCAL_API` is set, so there is nothing to pass and nothing to get
wrong. Passing it on the command line from Git Bash on Windows is in fact the
wrong thing: the shell rewrites a value that looks like a unix path, so
`NEXT_PUBLIC_API_URL=/api` arrives as `C:/Program Files/Git/api` and every
fetch fails on an unparseable URL. That surfaces as `network error: the API
did not respond`, which blames the API for something the shell did.

## Grading and sign-in without LocalStack Pro or GitHub

Two local-only switches, declared on the served functions by `localize.py` and
never in the deployed template:

- `BUGFORGE_LOCAL_GRADING=1` -- `fn_api` runs `fn_grade` and `fn_reveal` in its
  own process instead of invoking them. It already runs in the same image, repo
  and test suite included, so the verdicts are the real grader's (apply patch,
  full suite, PASS/FAIL/REJECTED). It runs on a thread because the suite
  outlasts the function's 29s timeout.
- `BUGFORGE_LOCAL_USER=local-dev` -- every request is signed in as that user, so
  submitting works with no OAuth app.

Both are ignored unless `AWS_ENDPOINT_URL` is also set, so a real deployment
cannot honour them even if a flag leaks in. `seed.py` also rewrites CRLF to LF
in the seeded trees: they were packaged on Windows, and `git apply` matches
context byte for byte, so with CRLF every patch -- the correct one too -- is
rejected as `patch_did_not_apply`.

## GitHub sign-in, locally

Sign-in is the one part that still talks to a real external service, because
only github.com can say who you are. It is optional: every screen except
submitting a patch works signed out.

1. Create an OAuth app at GitHub → Settings → Developer settings → OAuth Apps.
2. Homepage `http://localhost:3100`, **Authorization callback URL**
   `http://localhost:3100/api/auth/callback`. GitHub accepts `http` for
   localhost. It compares this string exactly, so it must match
   `OAuthRedirectUri`, which `deploy.sh` derives from `WEB_ORIGIN`.
3. Redeploy with the credentials in the environment, then restart the API so
   it picks up the new secret ARNs:

```bash
GITHUB_CLIENT_ID=Ov23li... GITHUB_CLIENT_SECRET=... ./infra/local/deploy.sh jd__tenacity
./infra/local/start_api.sh jd__tenacity
```

Restarting the API is not optional after a redeploy: `env_vars.py` reads the
function environment off the stack once, at start-up, so an API left running
across a redeploy still holds the previous stack's resource names.

**A redeploy does not change either GitHub secret.** LocalStack's
CloudFormation reports `UPDATE_COMPLETE` for a stack update but does not apply
a new `SecretString` to an `AWS::SecretsManager::Secret`; the secret keeps the
value it was created with. So a stack first deployed without sign-in keeps
`"unset"` in both GitHub secrets however many times you redeploy with
credentials, while the stack parameters update and everything looks correct.
The only symptom is the sign-in button bouncing off
`?auth_error=sign-in+is+not+configured`, which reads like a problem with the
OAuth app and is not one.

Secrets Manager is not what is broken here -- only CloudFormation's update
path is -- so `deploy.sh` reads both secrets back after the deploy and writes
the value through with `put-secret-value` when it does not match. Rerunning
the deploy with the credentials in the environment is the whole fix; the stack
does not need to be recreated, which would take the DynamoDB tables and the S3
artifacts with it:

```bash
GITHUB_CLIENT_ID=... GITHUB_CLIENT_SECRET=... ./infra/local/deploy.sh jd__tenacity
./infra/local/start_api.sh jd__tenacity   # restart it: see below
```

Restarting the API after a repair is not optional either. `start_api.sh` runs
with `--warm-containers EAGER` and `cloud/auth.py` caches each secret for the
life of its container, so an API left running across the repair keeps serving
the value it cached at start-up -- with exactly the symptom above.

`deploy.sh` also passes `InsecureCookies=true`, which drops `Secure` from the
session cookie and makes it `SameSite=Lax`. That is required, not a shortcut:
there is no TLS here, and a browser silently discards a `Secure` cookie from a
plain-http origin. The symptom if it is wrong is that sign-in appears to do
nothing -- the redirect succeeds and the cookie is simply never stored. Never
set it on a real deployment.

## One repo at a time

A stack carries one image, and an image carries one repo, so exactly one repo
is forgeable per stack -- that is what `GET /repos` reports in `forgeable`,
and what the landing page's chips are built from. To offer a second repo,
build its image and deploy a second stack with a different `STACK_NAME`.
