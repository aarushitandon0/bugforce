# One image per supported repo.
#
# Everything expensive happens HERE, at build time, on a trusted machine:
# the clone, the pinned checkout, and every pip install. Nothing is cloned or
# installed inside the Lambda at runtime, and the repo URL is baked in, so a
# request naming some other repo is refused (see fn_baseline) rather than
# quietly fetched.
FROM public.ecr.aws/lambda/python:3.12

# git is a runtime dependency, not just a build one: packaging a challenge
# re-inits a fresh history, and grading applies the learner's patch with
# `git apply`.
RUN dnf install -y git tar gzip && dnf clean all

ARG REPO_URL
ARG REPO_SHA
ARG REPO_PACKAGE
ARG REPO_EXTRAS="test"

RUN test -n "$REPO_URL" -a -n "$REPO_SHA" -a -n "$REPO_PACKAGE" \
    || (echo "REPO_URL, REPO_SHA and REPO_PACKAGE are required build args" && exit 1)

RUN git clone "$REPO_URL" /repo \
    && git -C /repo checkout --quiet "$REPO_SHA"

# The mutated copy of the tree under /tmp only shadows the installed package
# if the package sits at the repo root -- pytest prepends the rootdir, not a
# src/ subdirectory. An src-layout repo would silently run its tests against
# the UNMUTATED installed copy and grade every challenge as already-fixed, so
# fail the build here instead.
RUN test -d "/repo/$REPO_PACKAGE" || ( \
      echo "ERROR: /repo/$REPO_PACKAGE not found. This pipeline supports" \
           "root-layout packages only; $REPO_URL appears to use src/ layout." \
      && exit 1)

RUN pip install --no-cache-dir pytest pytest-cov coverage \
    && pip install --no-cache-dir -e "/repo[${REPO_EXTRAS}]" \
    && pip install --no-cache-dir "anthropic[bedrock]"

# Prove the suite is green in the image itself. A repo that cannot pass its
# own tests here has no usable baseline, and finding that out during a cloud
# run costs a lot more than finding it out now.
RUN cd /repo && python -m pytest -q --no-header -x -p no:cacheprovider \
    && rm -rf /repo/.pytest_cache

COPY bugforge ${LAMBDA_TASK_ROOT}/bugforge
COPY cloud ${LAMBDA_TASK_ROOT}/cloud

# REPO_LANGUAGE is explicit even though cloud/config.py defaults to it: now
# that a second language exists, relying on the default would make a
# mislabelled image fail as unparseable source rather than as a missing setting.
ENV REPO_DIR=/repo \
    REPO_LANGUAGE=python \
    HOME=/tmp \
    TMPDIR=/tmp \
    PYTEST_ADDOPTS="-p no:cacheprovider" \
    PYTHONDONTWRITEBYTECODE=1

# Overridden per function by ImageConfig.Command in the SAM template.
CMD ["cloud.handlers.fn_baseline.handler"]
