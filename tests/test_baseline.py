"""
Unit tests for bugforge.baseline against a tiny synthetic package + test
suite built on the fly. We don't depend on any vetted external repo here --
a silent bug in the line -> tests mapping would produce broken challenges we
wouldn't notice until demo day, so this needs to be exercised directly.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bugforge.baseline import BaselineError, compute_baseline


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)


def _write_green_package(repo_dir: Path) -> None:
    pkg = repo_dir / "mathy"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ops.py").write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def sub(a, b):\n"
        "    return a - b\n",
        encoding="utf-8",
    )
    tests = repo_dir / "tests"
    tests.mkdir()
    (tests / "test_ops.py").write_text(
        "from mathy.ops import add, sub\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n"
        "\n"
        "def test_sub():\n"
        "    assert sub(5, 3) == 2\n",
        encoding="utf-8",
    )
    _init_git_repo(repo_dir)


def _write_red_package(repo_dir: Path) -> None:
    pkg = repo_dir / "broken"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "ops.py").write_text(
        "def add(a, b):\n"
        "    return a + b + 1\n",  # deliberately wrong
        encoding="utf-8",
    )
    tests = repo_dir / "tests"
    tests.mkdir()
    (tests / "test_ops.py").write_text(
        "from broken.ops import add\n"
        "\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )
    _init_git_repo(repo_dir)


def test_compute_baseline_green_repo(tmp_path: Path):
    repo_dir = tmp_path / "greenrepo"
    _write_green_package(repo_dir)

    baseline = compute_baseline(repo_dir, package="mathy", python=sys.executable, use_cache=False)

    assert baseline.green is True
    assert baseline.total_tests == 2
    assert baseline.covered_line_count() > 0


def test_line_to_tests_maps_correct_tests(tmp_path: Path):
    repo_dir = tmp_path / "greenrepo2"
    _write_green_package(repo_dir)

    baseline = compute_baseline(repo_dir, package="mathy", python=sys.executable, use_cache=False)

    # `return a + b` is line 2 of mathy/ops.py -- only test_add should cover it.
    add_tests = baseline.tests_for_line("mathy/ops.py", 2)
    assert len(add_tests) == 1
    assert "test_add" in add_tests[0]

    # `return a - b` is line 5 -- only test_sub should cover it.
    sub_tests = baseline.tests_for_line("mathy/ops.py", 5)
    assert len(sub_tests) == 1
    assert "test_sub" in sub_tests[0]

    # the two lines must not be covered by each other's test
    assert add_tests != sub_tests


def test_compute_baseline_raises_on_red_repo(tmp_path: Path):
    repo_dir = tmp_path / "redrepo"
    _write_red_package(repo_dir)

    with pytest.raises(BaselineError, match="not green"):
        compute_baseline(repo_dir, package="broken", python=sys.executable, use_cache=False)


def test_compute_baseline_uses_cache(tmp_path: Path, monkeypatch):
    repo_dir = tmp_path / "greenrepo3"
    _write_green_package(repo_dir)

    first = compute_baseline(repo_dir, package="mathy", python=sys.executable, use_cache=True)

    # Corrupt the checked-out source after the first run; if the second call
    # actually re-ran pytest it would blow up (import error) instead of
    # silently succeeding from cache.
    (repo_dir / "mathy" / "ops.py").write_text("this is not valid python(((", encoding="utf-8")

    second = compute_baseline(repo_dir, package="mathy", python=sys.executable, use_cache=True)

    assert second.commit_sha == first.commit_sha
    assert second.line_to_tests == first.line_to_tests
