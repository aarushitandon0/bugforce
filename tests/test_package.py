"""
Unit tests for bugforge.package against the synthetic `mathy` repo. Checks
the properties the spec calls out explicitly: no upstream git history,
LICENSE preserved, README blanked / project name stripped in practice mode,
and two separate tarballs where the answers bundle contains only the patch.
"""
from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

import pytest

from bugforge.models import MutationSite
from bugforge.package import package_challenge
from bugforge.select import ClassificationResult, Outcome, ScoreBreakdown


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
    ops_source = "def add(a, b):\n    return a + b\n"
    (pkg / "ops.py").write_text(ops_source, encoding="utf-8")
    (repo_dir / "LICENSE").write_text("MIT License\n\nCopyright ...\n", encoding="utf-8")
    (repo_dir / "README.md").write_text("# Mathy\n\nA real project with real docs.\n", encoding="utf-8")
    (repo_dir / "pyproject.toml").write_text(
        '[project]\nname = "mathy"\nversion = "1.0"\n', encoding="utf-8"
    )
    _init_git_repo(repo_dir)
    return ops_source


def _admitted_result(site: MutationSite) -> ClassificationResult:
    return ClassificationResult(
        site=site,
        outcome=Outcome.ADMITTED,
        covering_tests=["tests/test_ops.py::test_add"],
        failing_tests=["tests/test_ops.py::test_add"],
        total_tests=2,
        traceback="mathy/ops.py:2: in add\n    return a - b\nE   assert 4 == 5",
        representative_test="tests/test_ops.py::test_add",
        score_breakdown=ScoreBreakdown(
            displacement=0, search_space=1, noise=0.5, name_leak=False, d=0.0, s=0.05, n=0.0, score=1.1125
        ),
    )


def test_package_challenge_produces_two_tarballs_and_json(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    ops_source = _write_mathy_repo(repo_dir)
    mutated_source = ops_source.replace("return a + b", "return a - b")
    site = MutationSite(
        path="mathy/ops.py",
        lineno=2,
        col_start=11,
        col_end=12,
        operator_id="ARITHMETIC",
        original_token="+",
        mutated_token="-",
        enclosing_function_name="add",
    )
    result = _admitted_result(site)
    output_dir = tmp_path / "out"

    challenge = package_challenge(
        repo_dir=repo_dir,
        repo_name="acme__mathy",
        commit_sha="abc1234567890",
        site=site,
        original_source=ops_source,
        mutated_source=mutated_source,
        classification=result,
        output_dir=output_dir,
    )

    public_tar = output_dir / f"{challenge.id}-public.tar.gz"
    answers_tar = output_dir / f"{challenge.id}-answers.tar.gz"
    json_path = output_dir / f"{challenge.id}.json"
    assert public_tar.exists()
    assert answers_tar.exists()
    assert json_path.exists()

    with tarfile.open(answers_tar) as tar:
        names = tar.getnames()
    assert names == ["mutation.patch"]

    # The public tree's root directory is visible to the learner, so it must
    # not be the challenge id (which names the file stem and line).
    with tarfile.open(public_tar) as tar:
        roots = {name.split("/", 1)[0] for name in tar.getnames()}
    assert roots == {"challenge"}
    assert "L2" not in " ".join(roots)


def test_git_history_is_fresh_single_commit_no_remote(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    ops_source = _write_mathy_repo(repo_dir)
    mutated_source = ops_source.replace("return a + b", "return a - b")
    site = MutationSite(
        path="mathy/ops.py", lineno=2, col_start=11, col_end=12, operator_id="ARITHMETIC",
        original_token="+", mutated_token="-", enclosing_function_name="add",
    )
    output_dir = tmp_path / "out"
    package_challenge(
        repo_dir, "acme__mathy", "abc1234567890", site, ops_source, mutated_source,
        _admitted_result(site), output_dir,
    )

    work_dir = output_dir / "work"
    log = subprocess.run(["git", "log", "--oneline"], cwd=work_dir, capture_output=True, text=True, check=True)
    commits = [l for l in log.stdout.strip().splitlines() if l]
    assert len(commits) == 1
    assert commits[0].endswith("initial")

    remotes = subprocess.run(["git", "remote"], cwd=work_dir, capture_output=True, text=True, check=True)
    assert remotes.stdout.strip() == ""


def test_licence_preserved_and_readme_blanked_in_practice_mode(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    ops_source = _write_mathy_repo(repo_dir)
    mutated_source = ops_source.replace("return a + b", "return a - b")
    site = MutationSite(
        path="mathy/ops.py", lineno=2, col_start=11, col_end=12, operator_id="ARITHMETIC",
        original_token="+", mutated_token="-", enclosing_function_name="add",
    )
    output_dir = tmp_path / "out"
    package_challenge(
        repo_dir, "acme__mathy", "abc1234567890", site, ops_source, mutated_source,
        _admitted_result(site), output_dir, practice_mode=True,
    )

    work_dir = output_dir / "work"
    licence_text = (work_dir / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in licence_text

    readme_text = (work_dir / "README.md").read_text(encoding="utf-8")
    assert readme_text == ""

    pyproject_text = (work_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert 'name = "mathy"' not in pyproject_text
    assert "practice-challenge" in pyproject_text


def test_rejects_non_admitted_classification(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    ops_source = _write_mathy_repo(repo_dir)
    site = MutationSite(
        path="mathy/ops.py", lineno=2, col_start=11, col_end=12, operator_id="ARITHMETIC",
        original_token="+", mutated_token="-", enclosing_function_name="add",
    )
    bad_result = ClassificationResult(site=site, outcome=Outcome.TEST_GAP)
    with pytest.raises(ValueError):
        package_challenge(
            repo_dir, "acme__mathy", "abc1234567890", site, ops_source,
            ops_source.replace("return a + b", "return a - b"), bad_result, tmp_path / "out2",
        )
