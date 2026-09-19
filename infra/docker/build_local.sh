#!/usr/bin/env bash
# Build the Lambda image for one vetted repo -- locally, no ECR, no account.
#
#   ./infra/docker/build_local.sh jd__tenacity
#
# The cloud path is build_and_push.sh; this is the same build with the
# registry steps removed, tagged with a plain local name that LocalStack can
# start containers from. Set BUGFORGE_CONTAINER=finch to build with Finch
# (AWS's open-source container CLI) instead of Docker; both take the same
# flags used here.
set -euo pipefail

REPO_KEY="${1:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${HERE}/../.." && pwd)"
VETTED="${HERE}/vetted_repos.json"
CONTAINER="${BUGFORGE_CONTAINER:-docker}"

if [[ -z "${REPO_KEY}" ]]; then
  echo "usage: $0 <repo_key>" >&2
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

case "${REPO_LANGUAGE}" in
  python) DOCKERFILE="${HERE}/Dockerfile" ;;
  go)     DOCKERFILE="${HERE}/Dockerfile.go" ;;
  *)      echo "no Dockerfile for language '${REPO_LANGUAGE}'" >&2; exit 1 ;;
esac

IMAGE="bugforge:${REPO_KEY}"

echo "==> ${REPO_KEY} @ ${REPO_SHA:0:10} (${REPO_LANGUAGE}, ${REPO_PACKAGE}) -> ${IMAGE} [${CONTAINER}]"

"${CONTAINER}" build \
  --platform linux/amd64 \
  --build-arg "REPO_URL=${REPO_URL}" \
  --build-arg "REPO_SHA=${REPO_SHA}" \
  --build-arg "REPO_PACKAGE=${REPO_PACKAGE}" \
  --build-arg "REPO_EXTRAS=${REPO_EXTRAS}" \
  -f "${DOCKERFILE}" \
  -t "${IMAGE}" \
  "${ROOT}"

echo
echo "built ${IMAGE}"
echo "deploy it into LocalStack with:"
echo "  ./infra/local/deploy.sh ${REPO_KEY}"
