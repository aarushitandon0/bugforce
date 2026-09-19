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

samlocal deploy   --template "${ROOT}/infra/template.yaml"   --stack-name "${STACK}"   --capabilities CAPABILITY_IAM   --no-confirm-changeset   --resolve-s3   --resolve-image-repos   --parameter-overrides "${PARAMS[@]}"

# The URL the web app should call (see api_url.py for why it is not the
# stack's own ApiUrl output verbatim).
API_URL="$(AWS_ENDPOINT_URL=http://localhost:4566 python "${HERE}/api_url.py" "${STACK}" 2>/dev/null || true)"

echo
echo "stack ${STACK} is up."
if [[ -n "${API_URL}" ]]; then
  echo "API base URL: ${API_URL}"
  echo
  echo "start the web app with:"
  echo "  cd web && BUGFORGE_LOCAL_API=${API_URL} NEXT_PUBLIC_API_URL=/api npm run dev -- --port 3100"
else
  echo "could not read the ApiUrl output; try:"
  echo "  AWS_ENDPOINT_URL=http://localhost:4566 python infra/local/api_url.py ${STACK}"
fi
