"""
Phase 3: package one admitted mutation into a learner-facing challenge tree
plus a separate, never-browser-presigned answers bundle.

Output layout under `output_dir`:
    work/                  the packaged tree (fresh git history, single commit)
    <challenge_id>-public.tar.gz    tree + traceback.txt  (learner-facing)
    <challenge_id>-answers.tar.gz   the mutation patch only (grading/answers)
    <challenge_id>.json             the Challenge record
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
from dataclasses import asdict
from pathlib import Path

from bugforge.ids import challenge_id
from bugforge.models import Challenge, MutationSite
from bugforge.select import ClassificationResult

PUBLIC_TREE_ROOT = "challenge"

_LICENCE_FILENAMES ={"LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING", "NOTICE"}
_README_FILENAMES = {"README.md", "README.rst", "README.txt", "README"}

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "bugforge",
    "GIT_AUTHOR_EMAIL": "bugforge@example.invalid",
    "GIT_COMMITTER_NAME": "bugforge",
    "GIT_COMMITTER_EMAIL": "bugforge@example.invalid",
}


def _run_git(args: list[str], cwd: Path) -> None:
    env = {**os.environ, **_GIT_ENV}
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")


def _rmtree(path: Path) -> None:
    """shutil.rmtree that also removes read-only files. git writes its object
    files read-only, and on Windows that makes a plain rmtree of a previously
    packaged tree fail with PermissionError."""

    def make_writable_and_retry(func, target, _exc):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=make_writable_and_retry)


def _make_challenge_id(repo: str, commit_sha: str, site: MutationSite) -> str:
    """The one id for this mutation, the same one the cloud pipeline writes.

    This used to build its own -- `jd_tenacity-3e58094d3b-retry-L113` -- which
    named the file and the line in a string that ends up in URLs and S3 keys,
    and gave the same mutation a second identity depending on which entry point
    packaged it. Two ids for one mutation is how the same bug ends up in the
    challenges table twice.
    """
    return challenge_id(repo, commit_sha, site.path, site.lineno, site.operator_id, site.mutated_token)


def _strip_project_name_metadata(text: str) -> str:
    """Best-effort removal of the project name from pyproject.toml / setup.py
    / setup.cfg metadata, so a practice bundle doesn't trivially diff against
    the real upstream package listing."""
    text = re.sub(r'(?m)^(name\s*=\s*)["\'][^"\']*["\']', r'\1"practice-challenge"', text)
    text = re.sub(r'(?m)^(\s*name\s*=\s*)["\'][^"\']*["\']', r'\1"practice-challenge"', text)
    return text


def _apply_practice_mode(tree_dir: Path) -> None:
    for name in _README_FILENAMES:
        readme = tree_dir / name
        if readme.exists() and readme.is_file():
            readme.write_text("", encoding="utf-8")

    for name in ("pyproject.toml", "setup.cfg"):
        cfg = tree_dir / name
        if cfg.exists():
            cfg.write_text(_strip_project_name_metadata(cfg.read_text(encoding="utf-8")), encoding="utf-8")

    setup_py = tree_dir / "setup.py"
    if setup_py.exists():
        text = setup_py.read_text(encoding="utf-8")
        text = re.sub(r'(name\s*=\s*)["\'][^"\']*["\']', r'\1"practice-challenge"', text)
        setup_py.write_text(text, encoding="utf-8")


def _verify_licences_preserved(original_repo_dir: Path, tree_dir: Path) -> None:
    for name in _LICENCE_FILENAMES:
        src = original_repo_dir / name
        if src.exists():
            dst = tree_dir / name
            if not dst.exists():
                raise RuntimeError(f"licence file {name} was lost while packaging")


def package_challenge(
    repo_dir: Path,
    repo_name: str,
    commit_sha: str,
    site: MutationSite,
    original_source: str,
    mutated_source: str,
    classification: ClassificationResult,
    output_dir: Path,
    practice_mode: bool = True,
    title: str = "",
    description: str = "",
) -> Challenge:
    """Materializes the mutated tree, strips git history, preserves licence
    files, optionally scrubs identifying metadata, and emits the public and
    answers tarballs plus the Challenge JSON record."""
    if classification.outcome != "ADMITTED":
        raise ValueError(f"only ADMITTED candidates are packaged, got {classification.outcome}")

    challenge_id = _make_challenge_id(repo_name, commit_sha, site)
    output_dir.mkdir(parents=True, exist_ok=True)
    tree_dir = output_dir / "work"
    if tree_dir.exists():
        _rmtree(tree_dir)

    shutil.copytree(
        repo_dir,
        tree_dir,
        ignore=shutil.ignore_patterns(".git", ".coverage", ".pytest_cache", "__pycache__", "*.pyc"),
    )
    (tree_dir / site.path).write_text(mutated_source, encoding="utf-8")

    _verify_licences_preserved(repo_dir, tree_dir)

    if practice_mode:
        _apply_practice_mode(tree_dir)

    # fresh history: no upstream commits, no remote
    git_dir = tree_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir)
    _run_git(["init", "-q"], cwd=tree_dir)
    _run_git(["add", "-A"], cwd=tree_dir)
    _run_git(["commit", "-q", "-m", "initial"], cwd=tree_dir)

    diff_lines = list(
        difflib.unified_diff(
            original_source.splitlines(keepends=True),
            mutated_source.splitlines(keepends=True),
            fromfile=f"a/{site.path}",
            tofile=f"b/{site.path}",
        )
    )
    patch_text = "".join(diff_lines)

    traceback_path = tree_dir / "traceback.txt"
    traceback_path.write_text(classification.traceback or "(no traceback captured)", encoding="utf-8")

    public_tar = output_dir / f"{challenge_id}-public.tar.gz"
    with tarfile.open(public_tar, "w:gz") as tar:
        # The root directory name is visible to the learner, so it must not be
        # the challenge id above, which carries the file stem and line number.
        tar.add(tree_dir, arcname=PUBLIC_TREE_ROOT)

    # answers live in a separate archive -- never bundled with, or derivable
    # from, the public tree above.
    traceback_path.unlink()  # was only needed inside the public tarball
    answers_dir = output_dir / "answers_staging"
    answers_dir.mkdir(exist_ok=True)
    patch_path = answers_dir / "mutation.patch"
    patch_path.write_text(patch_text, encoding="utf-8")
    answers_tar = output_dir / f"{challenge_id}-answers.tar.gz"
    with tarfile.open(answers_tar, "w:gz") as tar:
        tar.add(patch_path, arcname="mutation.patch")
    shutil.rmtree(answers_dir)

    challenge = Challenge(
        id=challenge_id,
        repo=repo_name,
        commit_sha=commit_sha,
        file_path=site.path,
        lineno=site.lineno,
        mutator_name=site.operator_id,
        diff=patch_text,
        failing_tests=classification.failing_tests,
        difficulty_score=classification.score_breakdown.score if classification.score_breakdown else 0.0,
        title=title,
        description=description,
    )

    challenge_json_path = output_dir / f"{challenge_id}.json"
    challenge_json_path.write_text(json.dumps(asdict(challenge), indent=2), encoding="utf-8")

    return challenge
