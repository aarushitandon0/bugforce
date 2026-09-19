#!/usr/bin/env bash
# Serve the stack's HTTP routes locally, because LocalStack cannot.
#
#   ./infra/local/start_api.sh          # then leave it running
#
# LocalStack community has no apigatewayv2. The stack still deploys -- every
# other resource is supported -- but AWS::ApiGatewayV2::Api is created as a
# stub, which is why the deploy's ApiUrl output reads `unknown.execute-api`.
# There is no endpoint behind it.
#
# So the HTTP layer is the one piece LocalStack does not serve, and `sam local
# start-api` serves it instead, reading the SAME template: it turns each
# function's HttpApi events into real routes and invokes the function in a
# container per request. Everything underneath -- S3, DynamoDB, Secrets
# Manager, Step Functions, and the eight pipeline lambdas Step Functions
# invokes -- stays in LocalStack, reached over its docker network.
#
# The split is exactly the coverage gap and nothing more. Two caveats worth
# knowing: API Gateway's own CORS handling is not emulated here (it does not
# need to be -- the dev server proxy makes every call same-origin), and each
# route pays a container start unless --warm-containers holds them open.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
STACK="${STACK_NAME:-bugforge-local}"
PORT="${API_PORT:-3101}"
REPO_KEY="${1:-jd__tenacity}"

# The containers SAM starts are not LocalStack's containers, so they reach it
# by container name on its network rather than on localhost, which inside a
# container is the container itself.
NETWORK="${LOCALSTACK_NETWORK:-local_default}"
CONTAINER="${LOCALSTACK_CONTAINER:-bugforge-localstack}"
LAMBDA_ENDPOINT="http://${CONTAINER}:4566"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

SAM="$(command -v sam || true)"
if [[ -z "${SAM}" ]]; then
  # The SAM CLI installs as sam.cmd on Windows and is not always on PATH in
  # Git Bash, even when samlocal (which wraps it) is. Test -e, not -x: a .cmd
  # on this filesystem is not flagged executable but runs perfectly well.
  for c in "$(command -v sam.cmd || true)" "/c/Program Files/Amazon/AWSSAMCLI/bin/sam.cmd"; do
    [[ -n "${c}" && -e "${c}" ]] && SAM="${c}" && break
  done
fi
[[ -n "${SAM}" ]] || { echo "sam not found -- install the AWS SAM CLI" >&2; exit 1; }

docker inspect "${CONTAINER}" >/dev/null 2>&1 || {
  echo "${CONTAINER} is not running -- docker compose -f infra/local/docker-compose.yml up -d" >&2
  exit 1
}

# Nothing stops a second server binding the same port on Windows, and when two
# are up the requests split between them unpredictably -- which looks like the
# API intermittently serving stale data rather than like two servers. Refuse
# instead, since the running one is usually the one that should be kept.
if (exec 3<>"/dev/tcp/127.0.0.1/${PORT}") 2>/dev/null; then
  exec 3>&-
  echo "port ${PORT} is already serving -- stop that one first, or set API_PORT" >&2
  exit 1
fi

# The environment comes off the deployed functions rather than being restated;
# env_vars.py says why.
BUILD="${ROOT}/infra/local/.build"
mkdir -p "${BUILD}"
AWS_ENDPOINT_URL=http://localhost:4566 \
  python "${HERE}/env_vars.py" "${STACK}" "${BUILD}"

# The localized template, with the endpoint declared on the two functions SAM
# serves. localize.py says why that cannot be passed on the command line.
TEMPLATE="$(python "${HERE}/localize.py" "${LAMBDA_ENDPOINT}")"

echo
echo "serving the stack's HTTP routes on http://127.0.0.1:${PORT}"
echo "start the web app in another terminal with:"
# NEXT_PUBLIC_API_URL is deliberately not printed here: next.config.mjs sets it
# to /api whenever BUGFORGE_LOCAL_API is set, and passing it from Git Bash is
# actively wrong -- the shell rewrites /api to a Windows path before node sees
# it, and every fetch then fails as "network error: the API did not respond".
echo "  cd web && BUGFORGE_LOCAL_API=http://127.0.0.1:${PORT} npm run dev -- --port 3100"
echo

exec "${SAM}" local start-api \
  --template "${TEMPLATE}" \
  --port "${PORT}" \
  --host 127.0.0.1 \
  --docker-network "${NETWORK}" \
  --env-vars "${BUILD}/env.json" \
  --warm-containers EAGER \
  --parameter-overrides "ImageUri=bugforge:${REPO_KEY}"
