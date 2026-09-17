"""Deterministic id construction shared by the pipeline lambdas."""
from __future__ import annotations

import hashlib


def site_digest(path: str, lineno: int, operator_id: str, mutated_token: str) -> str:
    """A stable, opaque fingerprint of one mutation site."""
    return hashlib.sha256(
        f"{path}:{lineno}:{operator_id}:{mutated_token}".encode("utf-8")
    ).hexdigest()[:12]


def challenge_id(repo_name: str, commit_sha: str, path: str, lineno: int, operator_id: str, mutated_token: str) -> str:
    """Stable id for one mutation.

    The id is public: it appears in every URL, API response, and S3 key. It
    therefore carries the repo and commit (both shown to learners anyway) and
    an opaque digest of the site -- never the file name or the line number,
    because a learner who reads those off the URL has already solved it.
    """
    safe_repo = repo_name.replace("/", "__")
    return f"{safe_repo}-{commit_sha[:10]}-{site_digest(path, lineno, operator_id, mutated_token)}"


def batch_id(index: int) -> str:
    return f"batch-{index:04d}"
