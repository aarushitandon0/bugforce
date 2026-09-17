"""Lambda filesystem setup.

The target repo and all of its dependencies are baked into the container
image at build time -- nothing is cloned or pip-installed at runtime. /var/task
and /repo are read-only, so anything that writes (coverage's .coverage file,
git, pytest) works on a copy under /tmp.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import bugforge.baseline as baseline_module

TMP = Path("/tmp")
WORK_DIR = TMP / "work"
_READY_MARKER = WORK_DIR / ".bugforge-ready"


def python_exe() -> str:
    """The interpreter the repo's dependencies were installed into at build time."""
    return sys.executable


def configure() -> None:
    """Redirects every write path in the Phase 1-3 code into /tmp.

    bugforge.baseline caches to a directory next to the package, which is on
    the read-only image layer inside Lambda.
    """
    baseline_module.CACHE_DIR = TMP / "baseline_cache"
    os.environ.setdefault("HOME", "/tmp")
    os.environ.setdefault("TMPDIR", "/tmp")
    # pytest's cache directory would otherwise be written inside the tree copy
    # on every single run, for no benefit here.
    os.environ.setdefault("PYTEST_ADDOPTS", "-p no:cacheprovider")


def repo_tree(source: str | os.PathLike[str]) -> Path:
    """A writable copy of the baked-in repo, reused across warm invocations.

    The copy keeps .git, because compute_baseline() reads HEAD to pin the
    commit sha.
    """
    configure()
    if _READY_MARKER.exists():
        return WORK_DIR

    if WORK_DIR.exists():
        shutil.rmtree(WORK_DIR)
    shutil.copytree(
        source,
        WORK_DIR,
        ignore=shutil.ignore_patterns(".coverage", ".pytest_cache", "__pycache__", "*.pyc"),
    )
    _READY_MARKER.write_text("ok", encoding="utf-8")
    return WORK_DIR


def scratch(name: str) -> Path:
    """A fresh empty scratch directory under /tmp."""
    path = TMP / name
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path
