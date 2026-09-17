"""State 5: the one model call in the system.

It writes a title and a two-sentence description. It does not choose the bug,
grade the fix, or influence the difficulty score -- all of that already
happened deterministically upstream. With BUGFORGE_DISABLE_BEDROCK set, or
when Bedrock is throttled or its output fails the post-check twice, every
challenge still gets the deterministic template copy.
"""
from __future__ import annotations

import logging
from pathlib import Path

from bugforge.models import MutationSite
from bugforge.select import Outcome

from cloud import config, describe as describe_module, s3_io

log = logging.getLogger()
log.setLevel(logging.INFO)

# Below this, stop calling Bedrock and template the rest -- prose is never
# worth timing out a run over.
RESERVE_MS = 30_000


def handler(event: dict, context) -> dict:
    execution_id = event["execution_id"]
    bucket = config.bucket()
    scored = s3_io.get_json(bucket, event["scored_key"])
    # Read-only is enough: we only need the module docstring and the scope
    # around the mutated line, so there's no reason to copy the tree to /tmp.
    repo = Path(config.repo_dir())

    described = []
    counts = {"bedrock": 0, "template": 0}
    for record in scored["scored"]:
        if record["outcome"] != Outcome.ADMITTED:
            described.append(record)
            continue

        site = MutationSite(**record["site"])
        source = (repo / site.path).read_text(encoding="utf-8")
        failing_test = record.get("representative_test") or record["failing_tests"][0]
        inp = describe_module.build_input(site, source, failing_test, record.get("traceback", ""))

        if context.get_remaining_time_in_millis() > RESERVE_MS:
            result = describe_module.describe(inp)
        else:
            result = describe_module.fallback_description(inp)

        counts[result.source] += 1
        described.append(
            {
                **record,
                "title": result.title,
                "description": result.description,
                "description_source": result.source,
                "description_rejections": result.rejections,
            }
        )

    key = s3_io.put_json(
        bucket, config.described_key(execution_id), {**scored, "scored": described}
    )
    log.info("described %s bedrock / %s template", counts["bedrock"], counts["template"])

    return {
        "execution_id": execution_id,
        "described_key": key,
        "bedrock_count": counts["bedrock"],
        "template_count": counts["template"],
    }
