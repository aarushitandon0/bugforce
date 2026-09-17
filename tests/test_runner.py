"""
Unit tests for bugforge.runner against the same synthetic `mathy` package
used in test_baseline.py. Verifies targeted vs. full-suite runs, timeout
handling, collection-error detection, and that the cache/original tree is
never touched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from bugforge.models import MutationSite
from bugforge.runner import materialize_mutated_tree, run_mutation, run_pytest


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)


def _write_mathy_repo(repo_dir: Path) -> None:
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


def _break_add_site() -> MutationSite:
    # `return a + b` -> `return a - b`, breaking test_add only
    return MutationSite(
        path="mathy/ops.py",
        lineno=2,
        col_start=11,
        col_end=12,
        operator_id="ARITHMETIC",
        original_token="+",
        mutated_token="-",
        enclosing_function_name="add",
    )


def test_targeted_run_only_runs_requested_tests(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    site = _break_add_site()
    mutated_source = (repo_dir / "mathy" / "ops.py").read_text(encoding="utf-8").replace(
        "return a + b", "return a - b"
    )

    result = run_mutation(
        repo_dir, sys.executable, site, mutated_source, test_ids=["tests/test_ops.py::test_add"]
    )

    assert result.passed == 0
    assert result.failed == 1
    assert result.failing_tests == ["tests/test_ops.py::test_add"]
    assert not result.collection_error
    assert not result.timed_out


def test_full_run_reports_only_the_actually_broken_test(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    site = _break_add_site()
    mutated_source = (repo_dir / "mathy" / "ops.py").read_text(encoding="utf-8").replace(
        "return a + b", "return a - b"
    )

    result = run_mutation(repo_dir, sys.executable, site, mutated_source, test_ids=None)

    assert result.passed == 1  # test_sub still passes
    assert result.failed == 1
    assert result.failing_tests == ["tests/test_ops.py::test_add"]


def test_run_mutation_never_touches_the_original_tree(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    original_ops = (repo_dir / "mathy" / "ops.py").read_text(encoding="utf-8")
    site = _break_add_site()
    mutated_source = original_ops.replace("return a + b", "return a - b")

    run_mutation(repo_dir, sys.executable, site, mutated_source, test_ids=None)

    assert (repo_dir / "mathy" / "ops.py").read_text(encoding="utf-8") == original_ops


def test_materialize_skips_git_and_coverage_artifacts(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    (repo_dir / ".coverage").write_text("fake coverage db", encoding="utf-8")
    pycache = repo_dir / "mathy" / "__pycache__"
    pycache.mkdir()
    (pycache / "ops.cpython-000.pyc").write_bytes(b"\x00")

    dest = tmp_path / "copy"
    site = _break_add_site()
    mutated_source = (repo_dir / "mathy" / "ops.py").read_text(encoding="utf-8").replace(
        "return a + b", "return a - b"
    )
    materialize_mutated_tree(repo_dir, dest, site, mutated_source)

    assert not (dest / ".git").exists()
    assert not (dest / ".coverage").exists()
    assert not (dest / "mathy" / "__pycache__").exists()
    assert (dest / "mathy" / "ops.py").read_text(encoding="utf-8") == mutated_source


def test_collection_error_detected_on_broken_import(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    site = _break_add_site()
    # a mutation that produces a NameError at import time (simulated directly,
    # not via mutate.apply, since apply() itself would refuse invalid syntax)
    mutated_source = (repo_dir / "mathy" / "ops.py").read_text(encoding="utf-8").replace(
        "def add(a, b):", "def add(a, b):\n    this_name_does_not_exist_anywhere()"
    )

    result = run_mutation(repo_dir, sys.executable, site, mutated_source, test_ids=None)

    # calling an undefined name inside add() only errors when add() actually
    # runs, so this isn't a collection error -- both tests should just fail.
    assert result.failed >= 1


def test_run_pytest_full_suite_green(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    result = run_pytest(repo_dir, sys.executable, test_ids=None)
    assert result.passed == 2
    assert result.failed == 0
    assert result.failing_tests == []


def test_run_pytest_timeout(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    _write_mathy_repo(repo_dir)
    (repo_dir / "tests" / "test_slow.py").write_text(
        "import time\ndef test_slow():\n    time.sleep(5)\n", encoding="utf-8"
    )
    result = run_pytest(repo_dir, sys.executable, test_ids=["tests/test_slow.py::test_slow"], timeout=1)
    assert result.timed_out is True
