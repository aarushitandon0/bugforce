# One image per supported Go repo.
#
# Same contract as the Python Dockerfile: everything expensive happens HERE, at
# build time, on a trusted machine -- the clone, the pinned checkout, the
# module download. Nothing is fetched inside the Lambda at runtime, and the
# repo URL is baked in, so a request naming some other repo is refused (see
# fn_baseline) rather than quietly cloned.
#
# The base is still the Python Lambda runtime, because the handlers are Python.
# Go is added as a tool the handlers shell out to, not as the runtime.
FROM public.ecr.aws/lambda/python:3.12

ARG GO_VERSION=1.26.8
# Pinned by checksum, not just by version: the toolchain is the thing that
# compiles the code we hand learners, so a swapped tarball is a supply-chain
# problem rather than a broken build. From https://go.dev/dl/.
ARG GO_SHA256=d0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b

# git is a runtime dependency, not just a build one: packaging a challenge
# re-inits a fresh history, and grading applies the learner's patch with
# `git apply`.
RUN dnf install -y git tar gzip diffutils && dnf clean all

RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" -o /tmp/go.tar.gz \
    && echo "${GO_SHA256}  /tmp/go.tar.gz" | sha256sum -c - \
    && tar -C /usr/local -xzf /tmp/go.tar.gz \
    && rm /tmp/go.tar.gz

ENV PATH="/usr/local/go/bin:${PATH}"

ARG REPO_URL
ARG REPO_SHA
ARG REPO_PACKAGE

RUN test -n "$REPO_URL" -a -n "$REPO_SHA" -a -n "$REPO_PACKAGE" \
    || (echo "REPO_URL, REPO_SHA and REPO_PACKAGE are required build args" && exit 1)

RUN git clone "$REPO_URL" /repo \
    && git -C /repo checkout --quiet "$REPO_SHA"

# The Go equivalent of the Python image's src/-layout check. REPO_PACKAGE is
# the module path from go.mod, and coverage profiles name every file by it --
# if it does not match, `_profile_to_lines` strips the wrong prefix and every
# line in the map is filed under a path that does not exist, which reads
# downstream as "this repo has no covered lines at all".
RUN cd /repo && test "$(go list -m)" = "$REPO_PACKAGE" || ( \
      echo "ERROR: go.mod says module $(cd /repo && go list -m)," \
           "but REPO_PACKAGE is $REPO_PACKAGE" \
      && exit 1)

# Go's build cache must be writable, and in Lambda only /tmp is. Building it
# into the image at /go-cache and copying it to /tmp on first use (see
# cloud/workspace.py) is what keeps a cold start from recompiling the standard
# library before it can run a single mutation.
ENV GOPATH=/go \
    GOMODCACHE=/go/pkg/mod \
    GOCACHE=/go-cache \
    CGO_ENABLED=0 \
    GOTOOLCHAIN=local

RUN cd /repo && go mod download

# Prove the suite is green in the image itself, and populate the build cache
# while doing it. A repo that cannot pass its own tests here has no usable
# baseline, and finding that out during a cloud run costs a lot more than
# finding it out now.
RUN cd /repo && go build ./... && go test -count=1 ./...

# Warm the cache for the shape of build the pipeline actually does: coverage
# instrumented, which is a different build id from the plain one above.
RUN cd /repo && go test -count=1 -covermode=set -coverpkg=./... \
      -coverprofile=/tmp/warm.out ./... && rm -f /tmp/warm.out

COPY bugforge ${LAMBDA_TASK_ROOT}/bugforge
COPY cloud ${LAMBDA_TASK_ROOT}/cloud

# Build the token locator once, here, so no Lambda invocation ever pays for a
# `go build` of it. cloud/workspace.py points BUGFORGE_GOLOCATE at the result.
RUN cd ${LAMBDA_TASK_ROOT}/bugforge/languages/golocate \
    && go build -o /usr/local/bin/golocate . \
    && echo '{"path":"x.go","source":"package x"}' | /usr/local/bin/golocate -mode=check >/dev/null

ENV REPO_DIR=/repo \
    REPO_LANGUAGE=go \
    BUGFORGE_GOLOCATE=/usr/local/bin/golocate \
    SEED_GOCACHE=/go-cache \
    HOME=/tmp \
    TMPDIR=/tmp \
    PYTHONDONTWRITEBYTECODE=1

# Overridden per function by ImageConfig.Command in the SAM template.
CMD ["cloud.handlers.fn_baseline.handler"]
