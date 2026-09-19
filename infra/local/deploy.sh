#!/usr/bin/env bash
# Deploy the SAM template into LocalStack. No AWS account, no credentials.
#
#   ./infra/local/deploy.sh jd__tenacity
#
# This is the same infra/template.yaml the cloud path uses -- same seven
# lambdas, same state machine, same two S3 prefixes. `samlocal` is the SAM CLI
# with its endpoint pointed at LocalStack (pip install aws-sam-cli-local), so
# the template is the one source of truth for both tracks.
set -euo pipefail

REPO_KEY="${1:-jd__tenacity}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
STACK="${STACK_NAME:-bugforge-local}"
WEB_ORIGIN="${WEB_ORIGIN:-http://localhost:3100}"
LOCALSTACK_ENDPOINT="${LOCALSTACK_ENDPOINT:-http://localhost:4566}"

# Credentials are required to exist, not to be valid: LocalStack accepts any
# string and checks nothing. These are the conventional dummy values.
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-test}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-test}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"

command -v samlocal >/dev/null 2>&1 || command -v samlocal.bat >/dev/null 2>&1 || {
  echo "samlocal not found -- pip install -r requirements-local.txt" >&2
  exit 1
}

read -r REPO_URL REPO_PACKAGE REPO_LICENSE < <(
  python - "${ROOT}/infra/docker/vetted_repos.json" "${REPO_KEY}" <<'PY'
import json, sys
for repo in json.load(open(sys.argv[1]))["repos"]:
    if repo["name"] == sys.argv[2]:
        print(repo["url"], repo["package"], repo.get("license") or "-")
        break
else:
    sys.exit(f"{sys.argv[2]} is not in the vetted repo list")
PY
)

# SAM rejects an empty parameter value outright ("GitHubClientId= is not a
# valid format"), so the sign-in pair is appended only when it is actually
# set. Leaving it out is a supported deploy: the stack comes up with sign-in
# switched off and every route but POST /submissions works.
PARAMS=(
  "ImageUri=bugforge:${REPO_KEY}"
  "RepoName=${REPO_KEY}"
  "RepoUrl=${REPO_URL}"
  "RepoPackage=${REPO_PACKAGE}"
  "RepoLicense=${REPO_LICENSE}"
  "WebOrigin=${WEB_ORIGIN}"
  "DisableBedrock=true"
  "InsecureCookies=true"
)
if [[ -n "${GITHUB_CLIENT_ID:-}" && -n "${GITHUB_CLIENT_SECRET:-}" ]]; then
  PARAMS+=(
    "GitHubClientId=${GITHUB_CLIENT_ID}"
    "GitHubClientSecret=${GITHUB_CLIENT_SECRET}"
    "OAuthRedirectUri=${WEB_ORIGIN}/api/auth/callback"
  )
  echo "==> deploying with GitHub sign-in enabled"
else
  echo "==> deploying with sign-in disabled (set GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET to enable)"
fi

# LocalStack's ASL parser rejects one field the deployed definition uses, so
# what gets deployed here is a generated copy rather than an edit to the real
# template. localize.py says exactly what differs and why.
TEMPLATE="$(python "${HERE}/localize.py")"

# --image-repository is required whenever any function is PackageType: Image,
# even though ImageUri here is a plain stack parameter that SAM cannot resolve
# locally and so never pushes anywhere. LocalStack community has no ECR at
# all, which is also why --resolve-image-repos (which would try to create one)
# is wrong here: the value only has to exist.
samlocal deploy \
  --template "${TEMPLATE}" \
  --stack-name "${STACK}" \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --resolve-s3 \
  --image-repository 000000000000.dkr.ecr.us-east-1.amazonaws.com/bugforge \
  --parameter-overrides "${PARAMS[@]}"

# LocalStack's CloudFormation reports UPDATE_COMPLETE for a stack update but
# does not apply the new SecretString to an AWS::SecretsManager::Secret -- the
# secret keeps whatever value it was created with. So a stack first deployed
# without sign-in keeps "unset" in both GitHub secrets no matter how many times
# it is redeployed with credentials, while the stack parameters update happily
# and everything looks right. The only symptom is the sign-in button bouncing
# off `?auth_error=sign-in+is+not+configured`, which reads like an OAuth app
# problem and is not one.
#
# Secrets Manager itself is fine here; it is only CloudFormation's update path
# that skips the value. So the repair is a direct put-secret-value, not a
# teardown -- which matters, because deleting the stack to recreate two strings
# would take the DynamoDB tables and the S3 artifacts with it. Verify rather
# than trust the deploy's exit status, and write the value through when it does
# not match.
SECRETS_REPAIRED=0
if [[ -n "${GITHUB_CLIENT_ID:-}" && -n "${GITHUB_CLIENT_SECRET:-}" ]]; then
  repair_secret() {
    local name="$1" want="$2" have
    have="$(aws --endpoint-url="${LOCALSTACK_ENDPOINT}" secretsmanager get-secret-value \
      --secret-id "${name}" --query SecretString --output text 2>/dev/null || true)"
    [[ "${have}" == "${want}" ]] && return 0
    aws --endpoint-url="${LOCALSTACK_ENDPOINT}" secretsmanager put-secret-value \
      --secret-id "${name}" --secret-string "${want}" >/dev/null
    echo "==> repaired ${name} (CloudFormation had left it at '${have}')"
    SECRETS_REPAIRED=1
    return 0
  }

  repair_secret "${STACK}-github-client-id" "${GITHUB_CLIENT_ID}"
  repair_secret "${STACK}-github-client-secret" "${GITHUB_CLIENT_SECRET}"
fi

# The stack's ApiUrl output is a dead letter here: LocalStack community has no
# apigatewayv2, so AWS::ApiGatewayV2::Api is created as a stub and the output
# reads `unknown.execute-api`. There is nothing behind it. start_api.sh serves
# those routes instead, which is why the web app is pointed at that and not at
# anything this deploy produced.
API_PORT="${API_PORT:-3101}"

echo
echo "stack ${STACK} is up."
echo
echo "the HTTP API is NOT part of this stack -- serve it next, and leave it running:"
echo "  ./infra/local/start_api.sh ${REPO_KEY}"
echo
echo "then, in another terminal:"
echo "  cd web && BUGFORGE_LOCAL_API=http://127.0.0.1:${API_PORT} NEXT_PUBLIC_API_URL=/api npm run dev -- --port 3100"

# start_api.sh runs with --warm-containers EAGER and auth.py caches each secret
# for the life of its container, so an API left running across this deploy is
# still holding the old value -- "unset" included. Restarting it is already
# required after any redeploy (env_vars.py reads the function environment once,
# at start-up); a repaired secret is one more reason, and the one whose symptom
# is indistinguishable from the bug this just fixed.
if [[ "${SECRETS_REPAIRED}" == "1" ]]; then
  echo
  echo "the GitHub secrets were repaired -- restart start_api.sh if it is running,"
  echo "or its warm containers will keep serving the value they cached at start-up."
fi
