"""Environment wiring and the S3 key layout.

Two S3 prefixes exist and only two:

    public/{challenge_id}/     tree.tar.gz, traceback.txt   -- presignable
    answers/{challenge_id}/    mutation.patch               -- never presigned

Pipeline intermediates live under answers/_work/ rather than a third
top-level prefix, so the "learner-facing tree vs. everything else" IAM split
stays exactly two prefixes wide. Challenge ids never begin with "_", so
_work can never collide with a real challenge.
"""
from __future__ import annotations

import os

PUBLIC_PREFIX = "public"
ANSWERS_PREFIX = "answers"
WORK_PREFIX = f"{ANSWERS_PREFIX}/_work"


def env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None:
        raise RuntimeError(f"required environment variable {name} is not set")
    return value


def bucket() -> str:
    return env("BUCKET")


def repo_dir() -> str:
    """Path to the target repo baked into the container image at build time."""
    return env("REPO_DIR", "/repo")


def repo_name() -> str:
    return env("REPO_NAME")


def repo_url() -> str:
    return env("REPO_URL")


def repo_package() -> str:
    """Import package inside the repo that coverage measures (e.g. "tenacity")."""
    return env("REPO_PACKAGE")


def repo_license() -> str:
    """SPDX id of the target repo's licence, shown on every challenge card."""
    return env("REPO_LICENSE", "")


def normalize_repo_url(url: str) -> str:
    """`https://github.com/JD/tenacity.git/` -> `https://github.com/jd/tenacity`."""
    return url.strip().rstrip("/").removesuffix(".git").lower()


def table(kind: str) -> str:
    return env(f"TABLE_{kind.upper()}")


# ---------------------------------------------------------------------------
# key layout
# ---------------------------------------------------------------------------

def work_prefix(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/"


def baseline_key(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/baseline.json"


def batch_prefix(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/batches/"


def batch_key(execution_id: str, batch_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/batches/{batch_id}.json"


def pending_key(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/pending.json"


def raw_result_key(execution_id: str, batch_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/raw/{batch_id}.json"


def raw_result_prefix(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/raw/"


def scored_key(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/scored.json"


def described_key(execution_id: str) -> str:
    return f"{WORK_PREFIX}/{execution_id}/described.json"


def public_tree_key(challenge_id: str) -> str:
    return f"{PUBLIC_PREFIX}/{challenge_id}/tree.tar.gz"


def public_traceback_key(challenge_id: str) -> str:
    return f"{PUBLIC_PREFIX}/{challenge_id}/traceback.txt"


def answer_patch_key(challenge_id: str) -> str:
    return f"{ANSWERS_PREFIX}/{challenge_id}/mutation.patch"


def answer_reveal_key(challenge_id: str) -> str:
    """The mutation site, for the post-PASS reveal. Same prefix, same IAM, as the patch."""
    return f"{ANSWERS_PREFIX}/{challenge_id}/reveal.json"
