"""The language seam: PythonAdapter must be a pure pass-through.

These tests exist to catch the seam drifting away from the code it wraps --
if someone "improves" PythonAdapter.find_candidates, the pipeline and the
direct callers stop agreeing and nothing else would notice.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from bugforge import mutate
from bugforge.languages import (
    DEFAULT_LANGUAGE,
    LanguageAdapter,
    PythonAdapter,
    UnsupportedLanguageError,
    available_languages,
    get_adapter,
)

SOURCE = textwrap.dedent(
    """\
    def clamp(x, lo=0):
        if x < lo:
            return lo
        return x
    """
)


def test_python_is_the_only_registered_language():
    assert available_languages() == ["python"]
    assert DEFAULT_LANGUAGE == "python"
    assert get_adapter().name == "python"
    assert get_adapter() is get_adapter("PYTHON")


def test_unknown_language_names_what_is_available():
    with pytest.raises(UnsupportedLanguageError) as exc:
        get_adapter("go")
    assert "go" in str(exc.value)
    assert "python" in str(exc.value)


def test_adapter_satisfies_the_protocol():
    assert isinstance(PythonAdapter(), LanguageAdapter)


def test_find_candidates_matches_the_underlying_function():
    adapter = get_adapter()
    assert adapter.find_candidates(SOURCE, "pkg/mod.py") == mutate.find_candidates(
        SOURCE, "pkg/mod.py"
    )


def test_apply_matches_the_underlying_function():
    adapter = get_adapter()
    site = mutate.find_candidates(SOURCE, "pkg/mod.py")[0]
    assert adapter.apply(SOURCE, site) == mutate.apply(SOURCE, site)


def test_apply_propagates_mutation_errors():
    adapter = get_adapter()
    site = mutate.find_candidates(SOURCE, "pkg/mod.py")[0]
    site.original_token = "definitely-not-there"
    with pytest.raises(mutate.MutationError):
        adapter.apply(SOURCE, site)


def _write(root: Path, rel: str, text: str = "x = 1\n") -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_discover_sources_skips_what_mutate_would_have_rejected(tmp_path):
    _write(tmp_path, "pkg/mod.py")
    _write(tmp_path, "pkg/util.py")
    _write(tmp_path, "tests/test_mod.py")
    _write(tmp_path, "pkg/test_helper.py")
    _write(tmp_path, "conftest.py")
    _write(tmp_path, "setup.py")
    _write(tmp_path, "docs/example.py")
    _write(tmp_path, "pkg/README.md", "not python\n")

    found = {str(p.relative_to(tmp_path)).replace("\\", "/") for p in get_adapter().discover_sources(tmp_path)}
    assert found == {"pkg/mod.py", "pkg/util.py"}


def test_discover_sources_skips_build_and_environment_directories(tmp_path):
    _write(tmp_path, "pkg/mod.py")
    _write(tmp_path, ".venv/lib/site-packages/thing.py")
    _write(tmp_path, "build/lib/pkg/mod.py")
    _write(tmp_path, "pkg/__pycache__/mod.py")

    found = [p.relative_to(tmp_path).as_posix() for p in get_adapter().discover_sources(tmp_path)]
    assert found == ["pkg/mod.py"]


def test_discover_sources_is_sorted_and_deterministic(tmp_path):
    for rel in ("pkg/z.py", "pkg/a.py", "pkg/sub/b.py"):
        _write(tmp_path, rel)
    adapter = get_adapter()
    assert adapter.discover_sources(tmp_path) == adapter.discover_sources(tmp_path)
    assert [p.name for p in adapter.discover_sources(tmp_path)] == ["a.py", "b.py", "z.py"]


def test_run_tests_forwards_to_run_pytest(monkeypatch, tmp_path):
    from bugforge import runner
    from bugforge.models import RunnerConfig

    seen = {}
    monkeypatch.setattr(
        runner, "run_pytest", lambda tree, python, ids, timeout: seen.update(
            tree=tree, python=python, ids=ids, timeout=timeout
        )
    )
    cfg = RunnerConfig(package="pkg", python="/usr/bin/python3", timeout_s=7)
    get_adapter().run_tests(tmp_path, ["tests/test_a.py::test_b"], cfg)

    assert seen == {
        "tree": tmp_path,
        "python": "/usr/bin/python3",
        "ids": ["tests/test_a.py::test_b"],
        "timeout": 7,
    }


def test_coverage_map_forwards_to_compute_baseline(monkeypatch, tmp_path):
    from bugforge import baseline as baseline_mod
    from bugforge.models import Baseline, RunnerConfig

    expected = {"pkg/mod.py:3": ["tests/test_mod.py::test_a"]}
    seen = {}

    def fake(repo_dir, package, python, use_cache=True):
        seen.update(repo_dir=repo_dir, package=package, python=python, use_cache=use_cache)
        return Baseline(repo="r", commit_sha="s", total_tests=1, green=True, line_to_tests=expected)

    monkeypatch.setattr(baseline_mod, "compute_baseline", fake)
    cfg = RunnerConfig(package="pkg", python="py", use_cache=False)

    assert get_adapter().coverage_map(tmp_path, cfg) == expected
    assert seen == {"repo_dir": tmp_path, "package": "pkg", "python": "py", "use_cache": False}
