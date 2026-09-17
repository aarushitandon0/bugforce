"""
Phase 0: Vet a candidate GitHub repo for use as a BugForge mutation source.

For a given GitHub URL:
  1. git clone --filter=blob:none into ./cache/{owner}__{repo} (reuse cache)
  2. report LOC of Python source, licence SPDX id, number of runtime deps,
     whether tests/ exists, test framework detected
  3. create a venv, install the package + test extras
  4. run the full suite, time it, report pass/fail counts
  5. flag red: needs network, needs a database, suite > 120s, suite not
     green, licence not in an approved allowlist

Usage:
    python scripts/vet_repo.py <github_url> [<github_url> ...]
    python scripts/vet_repo.py --json <github_url>   # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import venv
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "cache"

APPROVED_LICENCES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}
SUITE_TIME_LIMIT_S = 120

# Words that, if found (case-insensitive, whole word) in test source, suggest
# the suite talks to a real network or database rather than being hermetic.
NETWORK_HINTS = re.compile(r"\b(requests\.get|requests\.post|urlopen|socket\.connect|httpx\.(get|post)|live[_ ]?server)\b", re.I)
DB_HINTS = re.compile(r"\b(psycopg2|pymysql|sqlalchemy\.create_engine|redis\.Redis|pymongo|MongoClient)\b", re.I)

LICENCE_SPDX_HINTS = {
    "mit license": "MIT",
    "mit licence": "MIT",
    "apache license\nversion 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0",
    "bsd 3-clause": "BSD-3-Clause",
    "bsd 2-clause": "BSD-2-Clause",
    "redistributions of source code must retain the above copyright": "BSD-3-Clause",  # fallback, refined below
    "isc license": "ISC",
}


@dataclass
class VetResult:
    url: str
    slug: str = ""
    cloned: bool = False
    loc_python: int = 0
    licence_spdx: str | None = None
    runtime_deps: int = 0
    has_tests_dir: bool = False
    test_framework: str | None = None
    venv_ok: bool = False
    install_ok: bool = False
    suite_ran: bool = False
    suite_green: bool = False
    suite_time_s: float | None = None
    passed: int = 0
    failed: int = 0
    errors: int = 0
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_flag(self, msg: str) -> None:
        self.flags.append(msg)


def slug_for(url: str) -> str:
    url = url.rstrip("/")
    if url.endswith(".git"):
        url = url[: -len(".git")]
    parts = url.split("/")
    owner, repo = parts[-2], parts[-1]
    return f"{owner}__{repo}"


def run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True, env=env
    )


def clone_repo(url: str, dest: Path, result: VetResult) -> bool:
    if dest.exists() and (dest / ".git").exists():
        result.notes.append("reused cached clone")
        result.cloned = True
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = run(["git", "clone", "--filter=blob:none", url, str(dest)], timeout=300)
    if proc.returncode != 0:
        result.add_flag(f"clone failed: {proc.stderr.strip()[:300]}")
        return False
    result.cloned = True
    return True


def count_python_loc(repo_dir: Path) -> int:
    total = 0
    for path in repo_dir.rglob("*.py"):
        # skip vendored/build/test artefact directories that aren't source
        parts = set(path.relative_to(repo_dir).parts)
        if parts & {".git", "build", "dist", ".tox", ".venv", "venv", "__pycache__"}:
            continue
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#"):
                        total += 1
        except OSError:
            continue
    return total


def detect_licence(repo_dir: Path) -> str | None:
    for name in ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "COPYING"):
        path = repo_dir / name
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if "mit license" in text or "mit licence" in text:
            return "MIT"
        if "apache license" in text and "2.0" in text:
            return "Apache-2.0"
        if "redistributions in binary form" in text and "neither the name" in text:
            return "BSD-3-Clause"
        if "redistributions in binary form" in text and "neither the name" not in text:
            return "BSD-2-Clause"
        if "isc license" in text or ("permission to use, copy, modify" in text and "isc" in text):
            return "ISC"
        # Generic MIT boilerplate often ships without the literal word "MIT"
        # anywhere in the file (e.g. jsonschema's COPYING, rich's LICENSE) --
        # match on the distinctive permission + warranty-disclaimer language
        # instead of the license's own name.
        if (
            "permission is hereby granted, free of charge" in text
            and "the software is provided" in text
            and "without warranty of any kind" in text
        ):
            return "MIT"
        return "UNKNOWN"
    return None


def parse_runtime_deps(repo_dir: Path) -> int:
    pyproject = repo_dir / "pyproject.toml"
    setup_cfg = repo_dir / "setup.cfg"
    setup_py = repo_dir / "setup.py"

    if pyproject.exists():
        text = pyproject.read_text(encoding="utf-8", errors="ignore")
        try:
            import tomllib
            data = tomllib.loads(text)
        except Exception:
            data = {}
        deps = None
        proj = data.get("project", {}) if isinstance(data, dict) else {}
        if isinstance(proj, dict):
            deps = proj.get("dependencies")
        if deps is None:
            poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data, dict) else {}
            deps = poetry.get("dependencies")
            if isinstance(deps, dict):
                deps = [d for d in deps if d.lower() != "python"]
        if isinstance(deps, list):
            return len(deps)

    if setup_cfg.exists():
        text = setup_cfg.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"install_requires\s*=\s*((?:.|\n)*?)(?:\n\S|\Z)", text)
        if m:
            lines = [l.strip() for l in m.group(1).splitlines() if l.strip()]
            return len(lines)

    if setup_py.exists():
        text = setup_py.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"install_requires\s*=\s*\[(.*?)\]", text, re.S)
        if m:
            items = [i for i in m.group(1).split(",") if i.strip().strip("'\"")]
            return len(items)

    return 0


def _find_test_dirs(repo_dir: Path) -> list[Path]:
    """Finds test directories anywhere in the tree, not just at the top level --
    e.g. jsonschema keeps its suite at jsonschema/tests/, not ./tests/."""
    skip = {".git", "build", "dist", ".tox", ".venv", "venv", "__pycache__", "node_modules"}
    found = []
    for path in repo_dir.rglob("*"):
        if not path.is_dir() or path.name not in ("tests", "test"):
            continue
        if set(path.relative_to(repo_dir).parts) & skip:
            continue
        if any(path.glob("test_*.py")) or any(path.glob("*_test.py")):
            found.append(path)
    return found


def detect_test_framework(repo_dir: Path) -> tuple[bool, str | None]:
    test_dirs = _find_test_dirs(repo_dir)
    has_tests = bool(test_dirs)

    framework = None
    for cfg_name in ("pyproject.toml", "setup.cfg", "pytest.ini", "tox.ini"):
        cfg = repo_dir / cfg_name
        if cfg.exists() and "pytest" in cfg.read_text(encoding="utf-8", errors="ignore").lower():
            framework = "pytest"
            break
    if framework is None and has_tests:
        for tests_dir in test_dirs:
            for path in tests_dir.rglob("test_*.py"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                if "import pytest" in text or "from pytest" in text:
                    framework = "pytest"
                    break
                if "unittest" in text:
                    framework = "unittest"
            if framework:
                break
        if framework is None:
            framework = "pytest"  # default assumption for a tests dir with test_*.py files
    return has_tests, framework


def scan_hermeticity(repo_dir: Path, result: VetResult) -> None:
    for tests_dir in _find_test_dirs(repo_dir):
        for path in tests_dir.rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if NETWORK_HINTS.search(text):
                result.add_flag(f"possible network dependency in {path.relative_to(repo_dir)}")
            if DB_HINTS.search(text):
                result.add_flag(f"possible database dependency in {path.relative_to(repo_dir)}")


def parse_extra_test_deps(repo_dir: Path) -> list[str]:
    """Collects test/dev dependency package names that plain `pip install -e
    .[test]` may not surface: PEP 621 extras that fail to resolve on an
    editable dev build (seen on jsonschema), and Poetry's legacy
    dev-dependencies / dependency groups, which pip cannot read via extras at
    all (seen on rich). Best-effort: returns bare package names, since
    poetry-style version constraints ("^1.0") aren't pip-compatible syntax.
    """
    pyproject = repo_dir / "pyproject.toml"
    if not pyproject.exists():
        return []
    try:
        import tomllib
        data = tomllib.loads(pyproject.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []

    names: set[str] = set()

    proj = data.get("project", {})
    if isinstance(proj, dict):
        opt_deps = proj.get("optional-dependencies", {})
        if isinstance(opt_deps, dict):
            for key in ("test", "tests", "dev"):
                for spec in opt_deps.get(key, []) or []:
                    pkg = re.split(r"[<>=!~\[; ]", spec.strip())[0]
                    if pkg:
                        names.add(pkg)

    # PEP 735 dependency-groups (e.g. jsonschema's `[dependency-groups] test = [...]`)
    dep_groups = data.get("dependency-groups", {}) if isinstance(data, dict) else {}
    if isinstance(dep_groups, dict):
        for key in ("test", "tests", "dev"):
            for spec in dep_groups.get(key, []) or []:
                if not isinstance(spec, str):
                    continue  # skip {"include-group": "..."} entries
                pkg = re.split(r"[<>=!~\[; ]", spec.strip())[0]
                if pkg:
                    names.add(pkg)

    poetry = data.get("tool", {}).get("poetry", {}) if isinstance(data, dict) else {}
    if isinstance(poetry, dict):
        dev_deps = poetry.get("dev-dependencies", {}) or {}
        for pkg in dev_deps:
            if pkg.lower() != "python":
                names.add(pkg)
        groups = poetry.get("group", {}) or {}
        for group_name in ("dev", "test", "tests"):
            group_deps = groups.get(group_name, {}).get("dependencies", {}) if isinstance(groups.get(group_name), dict) else {}
            for pkg in group_deps:
                if pkg.lower() != "python":
                    names.add(pkg)

    return sorted(names)


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def setup_venv_and_install(repo_dir: Path, venv_dir: Path, result: VetResult) -> bool:
    try:
        if not venv_dir.exists():
            venv.EnvBuilder(with_pip=True).create(venv_dir)
        result.venv_ok = True
    except Exception as e:
        result.add_flag(f"venv creation failed: {e}")
        return False

    py = venv_python(venv_dir)
    proc = run([str(py), "-m", "pip", "install", "-U", "pip"], timeout=180)
    if proc.returncode != 0:
        result.add_flag(f"pip upgrade failed: {proc.stderr.strip()[:300]}")

    # try common test-extras spellings, then bare install, then pytest fallback
    attempts = [
        [str(py), "-m", "pip", "install", "-e", ".[test]"],
        [str(py), "-m", "pip", "install", "-e", ".[tests]"],
        [str(py), "-m", "pip", "install", "-e", ".[dev]"],
        [str(py), "-m", "pip", "install", "-e", "."],
    ]
    installed = False
    last_err = ""
    for cmd in attempts:
        proc = run(cmd, cwd=repo_dir, timeout=300)
        if proc.returncode == 0:
            installed = True
            break
        last_err = proc.stderr.strip()[-500:]
    if not installed:
        result.add_flag(f"pip install failed: {last_err[:300]}")
        return False

    # some repos (e.g. python-dotenv) declare test deps in a plain
    # requirements file rather than as installable extras -- pull those in too.
    for req_name in ("requirements-dev.txt", "requirements-test.txt", "requirements.txt"):
        req_file = repo_dir / req_name
        if req_file.exists():
            run([str(py), "-m", "pip", "install", "-r", str(req_file)], cwd=repo_dir, timeout=300)

    # some repos declare test deps via a PEP 621 extra that doesn't resolve on
    # an editable dev build, or via Poetry dev-dependencies/groups that plain
    # pip can't read via extras at all -- install those package names directly.
    extra_deps = parse_extra_test_deps(repo_dir)
    if extra_deps:
        run([str(py), "-m", "pip", "install", *extra_deps], timeout=300)

    # ensure pytest is present regardless of extras
    proc = run([str(py), "-m", "pip", "install", "pytest", "pytest-cov", "coverage"], timeout=180)
    if proc.returncode != 0:
        result.add_flag(f"pytest install failed: {proc.stderr.strip()[:300]}")
        return False

    result.install_ok = True
    return True


def run_suite(repo_dir: Path, venv_dir: Path, result: VetResult) -> None:
    py = venv_python(venv_dir)
    start = time.time()
    try:
        proc = run(
            [str(py), "-m", "pytest", "-q", "--no-header"],
            cwd=repo_dir,
            timeout=SUITE_TIME_LIMIT_S + 30,
        )
        elapsed = time.time() - start
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        result.suite_ran = True
        result.suite_time_s = round(elapsed, 1)
        result.add_flag(f"suite exceeded timeout ({SUITE_TIME_LIMIT_S + 30}s)")
        return

    result.suite_ran = True
    result.suite_time_s = round(elapsed, 1)
    output = proc.stdout + "\n" + proc.stderr

    m = re.search(r"(\d+) passed", output)
    result.passed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) failed", output)
    result.failed = int(m.group(1)) if m else 0
    m = re.search(r"(\d+) error", output)
    result.errors = int(m.group(1)) if m else 0

    result.suite_green = proc.returncode == 0 and result.failed == 0 and result.errors == 0

    if elapsed > SUITE_TIME_LIMIT_S:
        result.add_flag(f"suite took {elapsed:.1f}s (> {SUITE_TIME_LIMIT_S}s limit)")
    if not result.suite_green:
        result.add_flag(f"suite not green (passed={result.passed} failed={result.failed} errors={result.errors} exit={proc.returncode})")
        result.notes.append(output[-1500:])


def vet(url: str) -> VetResult:
    result = VetResult(url=url)
    slug = slug_for(url)
    result.slug = slug
    repo_dir = CACHE_DIR / slug

    if not clone_repo(url, repo_dir, result):
        return result

    result.loc_python = count_python_loc(repo_dir)
    result.licence_spdx = detect_licence(repo_dir)
    result.runtime_deps = parse_runtime_deps(repo_dir)
    result.has_tests_dir, result.test_framework = detect_test_framework(repo_dir)
    scan_hermeticity(repo_dir, result)

    if result.licence_spdx not in APPROVED_LICENCES:
        result.add_flag(f"licence '{result.licence_spdx}' not in approved allowlist")

    if not result.has_tests_dir:
        result.add_flag("no tests/ directory found")
        return result

    venv_dir = CACHE_DIR / f"{slug}__venv"
    if not setup_venv_and_install(repo_dir, venv_dir, result):
        return result

    run_suite(repo_dir, venv_dir, result)
    return result


def print_table(results: list[VetResult]) -> None:
    headers = ["repo", "LOC", "licence", "deps", "tests?", "framework", "green", "time(s)", "passed", "failed", "flags"]
    rows = []
    for r in results:
        rows.append([
            r.slug,
            str(r.loc_python),
            r.licence_spdx or "-",
            str(r.runtime_deps),
            "yes" if r.has_tests_dir else "no",
            r.test_framework or "-",
            "YES" if r.suite_green else "no",
            str(r.suite_time_s) if r.suite_time_s is not None else "-",
            str(r.passed),
            str(r.failed),
            "; ".join(r.flags)[:60] if r.flags else "-",
        ])
    widths = [max(len(h), *(len(row[i]) for row in rows)) if rows else len(h) for i, h in enumerate(headers)]
    def fmt(row):
        return " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(fmt(row))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("urls", nargs="+")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [vet(u) for u in args.urls]

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print_table(results)
        print()
        for r in results:
            if r.flags:
                print(f"[{r.slug}] flags:")
                for f in r.flags:
                    print(f"  - {f}")


if __name__ == "__main__":
    main()
