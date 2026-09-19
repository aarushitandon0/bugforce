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
import bugforge.runner as runner_module
from bugforge.languages import get_adapter
from bugforge.models import RunnerConfig

from cloud import config

TMP = Path("/tmp")
WORK_DIR = TMP / "work"
_READY_MARKER = WORK_DIR / ".bugforge-ready"
GOCACHE_DIR = TMP / "go-cache"
_GOCACHE_MARKER = GOCACHE_DIR / ".bugforge-seeded"


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
    _configure_go()


def _configure_go() -> None:
    """Gives the Go toolchain a writable build cache, seeded from the image.

    Go refuses to build without a writable GOCACHE, and in Lambda only /tmp
    is writable -- so pointing GOCACHE at the image's baked cache is not an
    option and leaving it unset means recompiling the standard library before
    the first mutation can run. The image builds a warm cache at
    $SEED_GOCACHE and this copies it to /tmp once per container; every
    invocation after that is incremental.

    Does nothing at all when SEED_GOCACHE is unset, which is every
    non-Lambda caller: a developer's machine already has its own GOCACHE and
    must keep it.
    """
    seed = os.environ.get("SEED_GOCACHE")
    if not seed:
        return
    os.environ["GOCACHE"] = str(GOCACHE_DIR)
    os.environ.setdefault("GOFLAGS", "-mod=mod")
    if _GOCACHE_MARKER.exists():
        return
    source = Path(seed)
    if source.is_dir():
        # dirs_exist_ok: a half-copied cache from a killed invocation is
        # still a valid cache -- Go treats a missing entry as a miss, not as
        # corruption -- so overwriting in place is safe and cheaper than
        # deleting first.
        shutil.copytree(source, GOCACHE_DIR, dirs_exist_ok=True)
    GOCACHE_DIR.mkdir(parents=True, exist_ok=True)
    _GOCACHE_MARKER.write_text("ok", encoding="utf-8")


def adapter():
    """The LanguageAdapter for the repo baked into this image."""
    return get_adapter(config.repo_language())


def runner_config(timeout_s: int) -> RunnerConfig:
    """How this image's adapter should invoke its toolchain.

    Both toolchain fields are always filled in. The adapter reads only the one
    it needs, and an adapter reading the wrong one is a bug worth failing on
    rather than a value worth withholding.
    """
    configure()
    return RunnerConfig(
        package=config.repo_package(),
        python=python_exe(),
        go=os.environ.get("GO_BIN", "go"),
        timeout_s=timeout_s,
        use_cache=False,
    )


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
        ignore=shutil.ignore_patterns(*runner_module.BUILD_ARTIFACTS),
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
