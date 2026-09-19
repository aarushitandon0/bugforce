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
| web | Next.js dev server | proxies `/api/*` to LocalStack so the session cookie is first-party |

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

# 4. the web app, pointed at the API the deploy printed
cd web
BUGFORGE_LOCAL_API=http://<api-id>.execute-api.localhost.localstack.cloud:4566 \
  NEXT_PUBLIC_API_URL=/api npm run dev -- --port 3100
```

`NEXT_PUBLIC_API_URL=/api` is not a detail. It makes the browser call the web
app's own origin, which the dev server proxies to LocalStack -- see
`web/next.config.mjs` for why sign-in needs that.

## GitHub sign-in, locally

Sign-in is the one part that still talks to a real external service, because
only github.com can say who you are. It is optional: every screen except
submitting a patch works signed out.

1. Create an OAuth app at GitHub → Settings → Developer settings → OAuth Apps.
2. Homepage `http://localhost:3100`, **Authorization callback URL**
   `http://localhost:3100/api/auth/callback`. GitHub accepts `http` for
   localhost. It compares this string exactly, so it must match
   `OAuthRedirectUri`, which `deploy.sh` derives from `WEB_ORIGIN`.
3. Redeploy with the credentials in the environment:

```bash
GITHUB_CLIENT_ID=Ov23li... GITHUB_CLIENT_SECRET=... ./infra/local/deploy.sh jd__tenacity
```

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
