"""Write a LocalStack-compatible copy of the template and the state machine.

Exactly one thing differs, and it is a gap in LocalStack's ASL parser rather
than anything wrong with the deployed definition:

    ToleratedFailurePercentage  -- real Step Functions, rejected by LocalStack
    ToleratedFailureCount       -- accepted by both

Distributed Map itself is supported; only the percentage form is not (probed
directly against the running container, not assumed). The count is derived
from the percentage against the batch count the generator actually produces,
so the local run tolerates the same *shape* of failure the deployed one does:
most batches raising NoSurvivorsError is the normal outcome, not a fault.

The copies go in a build directory so the deployed template stays the one
source of truth and nothing generated is ever edited by hand.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# infra/statemachine/forge_repo.asl.json fans out over $.batches, which
# fn_generate fills with this many batches (see cloud/handlers/fn_generate.py).
BATCH_COUNT = 15


def localize_asl(source: Path, target: Path) -> str:
    asl = json.loads(source.read_text(encoding="utf-8"))
    state = asl["States"]["RunBatches"]
    percentage = state.pop("ToleratedFailurePercentage", None)
    if percentage is None:
        note = "no ToleratedFailurePercentage to translate"
    else:
        count = (BATCH_COUNT * percentage) // 100
        state["ToleratedFailureCount"] = count
        note = f"ToleratedFailurePercentage {percentage} -> ToleratedFailureCount {count} of {BATCH_COUNT}"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asl, indent=2), encoding="utf-8")
    return note


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    build = root / "infra" / "local" / ".build"
    build.mkdir(parents=True, exist_ok=True)

    # The template is copied verbatim: DefinitionUri is resolved relative to
    # the template's own directory, so the copy finds the localized ASL beside
    # it at the same relative path.
    shutil.copy2(root / "infra" / "template.yaml", build / "template.yaml")
    note = localize_asl(
        root / "infra" / "statemachine" / "forge_repo.asl.json",
        build / "statemachine" / "forge_repo.asl.json",
    )
    print(f"localized: {note}", file=sys.stderr)
    print(build / "template.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
