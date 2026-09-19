#!/usr/bin/env bash
# Build the Lambda image for one vetted repo and push it to ECR.
#
#   ./infra/docker/build_and_push.sh jd__tenacity [region]
#
# Run from the project root. The repo must be listed in vetted_repos.json --
# that file is the allowlist, and the build reads the URL, commit sha, and
# package name from it rather than from the command line, so a typo can't
# bake an unvetted dependency set into an image.
set -euo pipefail

REPO_KEY="${1:-}"
REGION="${2:-${AWS_REGION:-us-east-1}}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
VETTED="${HERE}/vetted_repos.json"
ECR_REPO="${ECR_REPO:-bugforge}"

if [[ -z "${REPO_KEY}" ]]; then
  echo "usage: $0 <repo_key> [region]" >&2
  echo "vetted repos:" >&2
  python -c "import json,sys; [print(' ', r['name']) for r in json.load(open(sys.argv[1]))['repos']]" "${VETTED}" >&2
  exit 1
fi

read -r REPO_URL REPO_SHA REPO_PACKAGE REPO_EXTRAS REPO_LANGUAGE REPO_LICENSE < <(
  python - "${VETTED}" "${REPO_KEY}" <<'PY'
import json, sys
vetted, key = sys.argv[1], sys.argv[2]
for repo in json.load(open(vetted))["repos"]:
    if repo["name"] == key:
        # Fields are positional and must never be empty: an empty one would
        # shift every field after it in the shell's `read`. "-" stands in for
        # a value the entry does not set, and each Dockerfile ignores it.
        print(
            repo["url"],
            repo["sha"],
            repo["package"],
            repo.get("extras") or "-",
            repo.get("language", "python"),
            repo.get("license") or "-",
        )
        break
else:
    sys.exit(f"{key} is not in the vetted repo list; refusing to build")
PY
)

# One Dockerfile per language: a Go repo's image carries a Go toolchain and a
# Python repo's does not, and that is the same choice as which LanguageAdapter
# runs inside it (see bugforge/languages/).
case "${REPO_LANGUAGE}" in
  python) DOCKERFILE="${HERE}/Dockerfile" ;;
  go)     DOCKERFILE="${HERE}/Dockerfile.go" ;;
  *)      echo "no Dockerfile for language '${REPO_LANGUAGE}'" >&2; exit 1 ;;
esac

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
REGISTRY="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
TAG="${REPO_KEY}-${REPO_SHA:0:10}"
IMAGE="${REGISTRY}/${ECR_REPO}:${TAG}"

echo "==> ${REPO_KEY} @ ${REPO_SHA:0:10} (${REPO_LANGUAGE}, ${REPO_PACKAGE}) -> ${IMAGE}"

aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1 \
  || aws ecr create-repository \
       --repository-name "${ECR_REPO}" \
       --region "${REGION}" \
       --image-scanning-configuration scanOnPush=true >/dev/null

aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REGISTRY}"

# --provenance=false keeps the push to a plain image manifest; Lambda rejects
# the OCI attestation manifest that buildx attaches by default.
docker build \
  --platform linux/amd64 \
  --provenance=false \
  --build-arg "REPO_URL=${REPO_URL}" \
  --build-arg "REPO_SHA=${REPO_SHA}" \
  --build-arg "REPO_PACKAGE=${REPO_PACKAGE}" \
  --build-arg "REPO_EXTRAS=${REPO_EXTRAS}" \
  -f "${DOCKERFILE}" \
  -t "${IMAGE}" \
  "${ROOT}"

docker push "${IMAGE}"

echo
echo "pushed ${IMAGE}"
echo
echo "deploy with:"
echo "  sam deploy --template infra/template.yaml --stack-name bugforge \\"
echo "    --capabilities CAPABILITY_IAM --region ${REGION} --resolve-s3 \\"
echo "    --parameter-overrides ImageUri=${IMAGE} RepoName=${REPO_KEY} \\"
echo "      RepoUrl=${REPO_URL} RepoPackage=${REPO_PACKAGE} RepoLicense=${REPO_LICENSE}"
echo
# Deliberately not a stack parameter. The language is a property of the image
# -- a Go image has a Go toolchain in it and a Python image does not -- so it
# is baked in as REPO_LANGUAGE at build time. A parameter here could disagree
# with the image it is pointed at, and the failure would surface as every
# mutation being unparseable rather than as a deployment error.
echo "(language '${REPO_LANGUAGE}' is baked into the image, not a stack parameter)"
