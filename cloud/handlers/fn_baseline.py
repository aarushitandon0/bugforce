"""State 1: run the repo's full suite once with coverage contexts.

The repo is already in the image at a pinned commit -- this lambda refuses to
work on any other repo rather than cloning one at runtime.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from cloud import config, s3_io, workspace

# Per `go test` / `pytest` invocation, not for the step as a whole. The Go
# adapter runs one coverage profile per test, so the step's total is this
# times the test count -- which is why BaselineFunction gets Lambda's maximum
# 900s timeout in the template, and why a repo is only vettable if its whole
# per-test pass fits inside that.
BASELINE_TIMEOUT_S = 240

log = logging.getLogger()
log.setLevel(logging.INFO)


class UnvettedRepoError(RuntimeError):
    """Raised when the request names a repo this image was not built for."""


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    requested = event.get("repo_url") or config.repo_url()

    if config.normalize_repo_url(requested) != config.normalize_repo_url(config.repo_url()):
        raise UnvettedRepoError(
            f"this image is built for {config.repo_url()}; refusing to forge {requested}. "
            "Dependencies are installed at build time only -- deploy an image for that "
            "repo instead."
        )

    tree = workspace.repo_tree(config.repo_dir())
    # use_cache=False: the image pins one commit, so a cache hit could only
    # ever come from a previous invocation in this same container, and a stale
    # map is worse than a slow one.
    baseline = workspace.adapter().baseline(tree, workspace.runner_config(BASELINE_TIMEOUT_S))

    key = s3_io.put_json(config.bucket(), config.baseline_key(execution_id), asdict(baseline))
    log.info(
        "baseline: %s tests, %s covered lines at %s",
        baseline.total_tests,
        baseline.covered_line_count(),
        baseline.commit_sha,
    )

    return {
        "execution_id": execution_id,
        "baseline_key": key,
        "repo": config.repo_name(),
        "commit_sha": baseline.commit_sha,
        "total_tests": baseline.total_tests,
        "covered_lines": baseline.covered_line_count(),
    }
