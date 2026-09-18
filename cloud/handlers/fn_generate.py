"""State 2: locate AST candidates on covered lines and cut them into batches.

Only lines the baseline says are covered by at least one test are considered.
An uncovered line can never be caught by the suite, so it is a test gap by
definition and there is nothing to run.

Batches carry mutation *sites*, not mutated source: fn_run_batch re-applies
each site to the same baked-in file and gets a byte-identical result, so
there's no reason to ship whole files through S3.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path

from bugforge.languages import get_adapter
from bugforge.models import Baseline
from bugforge.mutate import MutationError, apply, find_candidates

from cloud import config, ids, s3_io, workspace

log = logging.getLogger()
log.setLevel(logging.INFO)

BATCH_SIZE = 15

# The only language we generate for. Asking the registry rather than reaching
# for mutate.py directly is what keeps this handler language-agnostic; see
# bugforge/languages/base.py.
ADAPTER = get_adapter()


def package_dir(tree: Path, package: str) -> Path:
    # Root layout only, and deliberately so: pytest prepends the rootdir of the
    # mutated copy to sys.path, which shadows the installed package only when
    # the package lives at the root. The image build enforces the same rule.
    candidate = tree / package
    if not candidate.is_dir():
        raise RuntimeError(f"package {package!r} not found at {candidate}")
    return candidate


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    baseline = Baseline(**s3_io.get_json(config.bucket(), event["baseline_key"]))

    tree = workspace.repo_tree(config.repo_dir())
    pkg = package_dir(tree, config.repo_package())

    sites: list[dict] = []
    for py_file in ADAPTER.discover_sources(pkg):
        rel = str(py_file.relative_to(tree)).replace("\\", "/")
        source = py_file.read_text(encoding="utf-8")
        try:
            found = find_candidates(source, rel)
        except SyntaxError:
            continue
        for site in found:
            if not baseline.tests_for_line(site.path, site.lineno):
                continue
            try:
                apply(source, site)  # proves it splices cleanly before we ship it
            except MutationError:
                continue
            sites.append(asdict(site))

    batches = []
    for i in range(0, len(sites), BATCH_SIZE):
        batch_id = ids.batch_id(len(batches))
        key = s3_io.put_json(
            config.bucket(),
            config.batch_key(execution_id, batch_id),
            {
                "execution_id": execution_id,
                "batch_id": batch_id,
                "baseline_key": event["baseline_key"],
                "sites": sites[i : i + BATCH_SIZE],
            },
        )
        batches.append(
            {
                "execution_id": execution_id,
                "batch_id": batch_id,
                "batch_key": key,
                "baseline_key": event["baseline_key"],
            }
        )

    log.info("generated %s covered candidates in %s batches", len(sites), len(batches))
    return {
        **event,
        "candidate_count": len(sites),
        "batch_count": len(batches),
        "batches": batches,
    }
