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
from bugforge.mutate import MutationError

from cloud import config, ids, s3_io, workspace

log = logging.getLogger()
log.setLevel(logging.INFO)

BATCH_SIZE = 15

# The image's repo decides the language, and the registry decides what that
# means. This handler never imports a language-specific module; see
# bugforge/languages/base.py.
ADAPTER = get_adapter(config.repo_language())


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    baseline = Baseline(**s3_io.get_json(config.bucket(), event["baseline_key"]))

    tree = workspace.repo_tree(config.repo_dir())
    root = ADAPTER.source_root(tree, config.repo_package())

    sites: list[dict] = []
    for source_file in ADAPTER.discover_sources(root):
        rel = str(source_file.relative_to(tree)).replace("\\", "/")
        source = source_file.read_text(encoding="utf-8")
        try:
            found = ADAPTER.find_candidates(source, rel)
        except SyntaxError:
            continue
        for site in found:
            if not baseline.tests_for_line(site.path, site.lineno):
                continue
            try:
                ADAPTER.apply(source, site)  # proves it splices before we ship it
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
