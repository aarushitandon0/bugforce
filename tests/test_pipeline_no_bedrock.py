"""The whole local pipeline, end to end, with BUGFORGE_DISABLE_BEDROCK set.

baseline -> candidates on covered lines -> targeted + full-suite selection ->
describe -> package. Real pytest runs, real coverage contexts, real tarballs.

If this fails, the fallback isn't real: the platform would depend on the one
model call. The `anthropic` module is poisoned and the client factory records
calls, so even a silently-swallowed model call would fail the test.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tarfile
from pathlib import Path

import bugforge.baseline as baseline_module
from bugforge.baseline import compute_baseline
from bugforge.mutate import MutationError, apply, find_candidates
from bugforge.package import package_challenge
from bugforge.select import Outcome, run_selection
from cloud import describe as describe_module

PRICING = '''"""Price calculations for a small shop: bulk discounts and order totals."""


def unit_price(quantity):
    if quantity >= 10:
        return 8
    return 10


def order_total(quantity):
    return unit_price(quantity) * quantity
'''

LABELS = '''"""Human-readable labels."""


def currency(amount):
    return "$" + str(amount)
'''

TESTS = """from shop.labels import currency
from shop.pricing import order_total


def test_bulk_order_is_discounted():
    assert order_total(10) == 80


def test_small_order_pays_full_price():
    assert order_total(3) == 30


def test_currency_whole():
    assert currency(5) == "$5"


def test_currency_zero():
    assert currency(0) == "$0"


def test_currency_large():
    assert currency(1000) == "$1000"


def test_currency_negative():
    assert currency(-2) == "$-2"
"""


def _make_repo(repo_dir: Path) -> None:
    (repo_dir / "shop").mkdir(parents=True)
    (repo_dir / "shop" / "__init__.py").write_text("", encoding="utf-8")
    (repo_dir / "shop" / "pricing.py").write_text(PRICING, encoding="utf-8")
    (repo_dir / "shop" / "labels.py").write_text(LABELS, encoding="utf-8")
    (repo_dir / "tests").mkdir()
    (repo_dir / "tests" / "test_shop.py").write_text(TESTS, encoding="utf-8")
    (repo_dir / "LICENSE").write_text("MIT License\n", encoding="utf-8")
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@example.com"],
        ["config", "user.name", "T"],
        ["add", "-A"],
        ["commit", "-q", "-m", "init"],
    ):
        subprocess.run(["git", *args], cwd=repo_dir, check=True)


def test_full_pipeline_runs_clean_with_bedrock_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(describe_module.DISABLE_ENV, "1")
    monkeypatch.setitem(sys.modules, "anthropic", None)
    model_calls = []
    monkeypatch.setattr(describe_module, "_client", lambda: model_calls.append(1))
    monkeypatch.setattr(baseline_module, "CACHE_DIR", tmp_path / "baseline_cache")

    repo = tmp_path / "shop_repo"
    _make_repo(repo)

    baseline = compute_baseline(repo, "shop", sys.executable, use_cache=False)
    assert baseline.green and baseline.total_tests == 6

    sources, pairs = {}, []
    for py_file in sorted((repo / "shop").rglob("*.py")):
        rel = str(py_file.relative_to(repo)).replace("\\", "/")
        sources[rel] = py_file.read_text(encoding="utf-8")
        for site in find_candidates(sources[rel], rel):
            if baseline.tests_for_line(site.path, site.lineno):
                try:
                    pairs.append((site, apply(sources[rel], site)))
                except MutationError:
                    pass
    assert pairs

    results, _ = run_selection(repo, sys.executable, baseline, pairs)
    admitted = [r for r in results if r.outcome == Outcome.ADMITTED]
    assert admitted, f"expected at least one admitted challenge, got {[r.outcome for r in results]}"

    for i, result in enumerate(admitted):
        site = result.site
        inp = describe_module.build_input(
            site, sources[site.path], result.representative_test, result.traceback
        )
        copy = describe_module.describe(inp)
        assert copy.source == "template"
        assert copy.rejections == []

        out = tmp_path / "out" / str(i)
        challenge = package_challenge(
            repo, "acme__shop", baseline.commit_sha, site, sources[site.path],
            apply(sources[site.path], site), result, out,
            title=copy.title, description=copy.description,
        )
        record = json.loads((out / f"{challenge.id}.json").read_text(encoding="utf-8"))
        assert record["title"] == copy.title
        assert record["description"] == copy.description
        with tarfile.open(out / f"{challenge.id}-answers.tar.gz") as tar:
            assert tar.getnames() == ["mutation.patch"]

    # The `>=` -> `>` flip is the one mutation this repo must admit, and its
    # copy is fully determined by the template.
    comparison = [r for r in admitted if r.site.operator_id == "COMPARISON"]
    assert len(comparison) == 1
    copy = describe_module.describe(
        describe_module.build_input(
            comparison[0].site, PRICING, comparison[0].representative_test, comparison[0].traceback
        )
    )
    assert copy.title == "Comparison in unit_price"
    assert copy.description == "test_bulk_order_is_discounted expected 80, got 100."

    assert model_calls == []
