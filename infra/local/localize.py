"""Write a LocalStack-compatible copy of the template and the state machine.

Two things differ from the deployed template, both of them gaps in what runs
locally rather than anything wrong with what is deployed.

The first is in the state machine, and applies to every local run:

    ToleratedFailurePercentage  -- real Step Functions, rejected by LocalStack
    ToleratedFailureCountPath   -- accepted by both

Distributed Map itself is supported; only the percentage form is not (probed
directly against the running container, not assumed). There is no constant
that translates it, because the denominator is not fixed: fn_generate cuts
len(sites) // 15 batches, so the batch count is a property of the repo and is
unbounded. It substitutes the count *path* instead, pointed at the
`$.batch_count` the generator already returns and the AnyCandidates choice
already reads, so the tolerance scales with the run rather than guessing it.

What that costs: tolerating batch_count failures tolerates all of them, where
90 percent still aborts a run in which every single batch failed. That alarm
is the one behavioural difference between this copy and the deployed
definition. Most batches raising NoSurvivorsError stays the normal outcome
under both, which is the property that actually has to survive here.

The second applies only when an endpoint is passed, which start_api.sh does
and deploy.sh does not. SAM serves the HTTP routes LocalStack cannot (see
start_api.sh), and those functions have to be told where LocalStack is. The
variable cannot come from --env-vars, which SAM applies only to names the
template already declares, nor from --container-env-vars, which is honoured
only in a debugging session; both were tried against this SAM and neither
reached the container. Declaring it here is what works, and it belongs in a
generated copy rather than the template, because a deployed function must
reach real AWS and must never carry an endpoint override.

The copies go in a build directory so the deployed template stays the one
source of truth and nothing generated is ever edited by hand.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# fn_generate returns batch_count beside the batches themselves, and the
# AnyCandidates choice already branches on it, so it is present in the input
# RunBatches receives.
BATCH_COUNT_PATH = "$.batch_count"


def localize_asl(source: Path, target: Path) -> str:
    asl = json.loads(source.read_text(encoding="utf-8"))
    state = asl["States"]["RunBatches"]
    percentage = state.pop("ToleratedFailurePercentage", None)
    if percentage is None:
        note = "no ToleratedFailurePercentage to translate"
    else:
        state["ToleratedFailureCountPath"] = BATCH_COUNT_PATH
        note = (
            f"ToleratedFailurePercentage {percentage} -> "
            f"ToleratedFailureCountPath {BATCH_COUNT_PATH}"
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(asl, indent=2), encoding="utf-8")
    return note


# The only two functions with HttpApi events, and so the only two SAM serves.
HTTP_FUNCTIONS = ("AuthFunction", "ApiFunction")


def inject_endpoint(template: str, endpoint: str, public_endpoint: str) -> str:
    """Declare the two endpoint overrides on the functions SAM will serve.

    A text insertion rather than a YAML round trip: the template is full of
    !Ref and !GetAtt, and there is no loader that reads those and a dumper
    that writes them back unchanged. The anchor is each function's own
    `Environment:`/`Variables:` pair, and both functions declare one already.

    AWS_ENDPOINT_URL is where the function's own calls go: LocalStack on the
    docker network. S3_PUBLIC_ENDPOINT_URL is where a *browser* reaches the
    same LocalStack, and only presigned URLs use it -- see cloud/s3_io.py. One
    address cannot serve both: the container name does not resolve in a
    browser, and localhost inside a container is the container.
    """
    declarations = (
        f"          AWS_ENDPOINT_URL: {endpoint}\n"
        f"          S3_PUBLIC_ENDPOINT_URL: {public_endpoint}\n"
        # Local-only stand-ins for what LocalStack community cannot provide:
        # container-image Lambdas (grading) and a GitHub OAuth app (sign-in).
        # fn_api ignores both unless AWS_ENDPOINT_URL is also set.
        "          BUGFORGE_LOCAL_GRADING: '1'\n"
        "          BUGFORGE_LOCAL_USER: local-dev\n"
    )
    for logical in HTTP_FUNCTIONS:
        anchor = f"\n  {logical}:\n"
        start = template.find(anchor)
        if start < 0:
            raise SystemExit(f"{logical} is not in the template")
        variables = template.find("\n      Environment:\n        Variables:\n", start)
        if variables < 0:
            raise SystemExit(f"{logical} declares no Environment.Variables")
        insert = variables + len("\n      Environment:\n        Variables:\n")
        template = template[:insert] + declarations + template[insert:]
    return template


def main(endpoint: str | None = None, public_endpoint: str | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    build = root / "infra" / "local" / ".build"
    build.mkdir(parents=True, exist_ok=True)

    # DefinitionUri is resolved relative to the template's own directory, so
    # the copy finds the localized ASL beside it at the same relative path.
    source = root / "infra" / "template.yaml"
    if endpoint is None:
        target = build / "template.yaml"
        shutil.copy2(source, target)
    else:
        # A separate name, so a deploy never picks up the endpoint override by
        # accident just because an API run wrote the file last.
        target = build / "template.api.yaml"
        public = public_endpoint or "http://localhost:4566"
        text = inject_endpoint(source.read_text(encoding="utf-8"), endpoint, public)
        target.write_text(text, encoding="utf-8")
        print(
            f"localized: AWS_ENDPOINT_URL={endpoint}, S3_PUBLIC_ENDPOINT_URL={public} "
            f"on {', '.join(HTTP_FUNCTIONS)}",
            file=sys.stderr,
        )

    note = localize_asl(
        root / "infra" / "statemachine" / "forge_repo.asl.json",
        build / "statemachine" / "forge_repo.asl.json",
    )
    print(f"localized: {note}", file=sys.stderr)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(
        main(
            sys.argv[1] if len(sys.argv) > 1 else None,
            sys.argv[2] if len(sys.argv) > 2 else None,
        )
    )
