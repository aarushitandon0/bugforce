"""Emit the --env-vars JSON that `sam local start-api` needs, read off the stack.

LocalStack community has no apigatewayv2, so the HTTP API the template declares
is the one resource that cannot be served here (see start_api.sh). SAM serves
those routes instead, which means the two HTTP-facing functions run outside
LocalStack's Lambda and get their environment from this file rather than from
CloudFormation.

The values are not restated here. CloudFormation already resolved every !Ref
and !GetAtt when it created the deployed functions, so this reads the deployed
environment back and hands it to SAM verbatim. A table renamed in the template
therefore needs no edit here, and the local API cannot drift from the local
stack the way a second hand-written copy of the wiring would.

What comes out is env.json, for SAM's --env-vars. Note that SAM applies such
a file ONLY to names the template already declares, and silently drops the
rest -- which is why AWS_ENDPOINT_URL is not here but in localize.py, where it
is declared on the two functions before SAM ever reads the template.

Getting that one wrong fails in a way worth recognising: with no endpoint the
calls leave for real AWS and come back `UnrecognizedClientException`, which
reads like a credentials problem and is really a routing one.

Usage:  python infra/local/env_vars.py <stack> <out dir>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import boto3

# The logical ids SAM matches against, which are also the only two functions
# with HttpApi events. The pipeline functions stay in LocalStack's Lambda:
# Step Functions invokes them there, and nothing here serves them.
HTTP_FUNCTIONS = ("ApiFunction", "AuthFunction")


def deployed_functions(stack: str) -> dict[str, str]:
    """logical id -> deployed physical function name, for the stack's Lambdas."""
    cfn = boto3.client("cloudformation")
    pages = cfn.get_paginator("list_stack_resources").paginate(StackName=stack)
    return {
        r["LogicalResourceId"]: r["PhysicalResourceId"]
        for page in pages
        for r in page["StackResourceSummaries"]
        if r["ResourceType"] == "AWS::Lambda::Function"
    }


def main(stack: str, out_dir: str) -> int:
    try:
        physical = deployed_functions(stack)
    except Exception as e:  # no stack, no endpoint, anything
        print(f"could not read stack {stack}: {e}", file=sys.stderr)
        return 1

    awslambda = boto3.client("lambda")
    env: dict[str, dict[str, str]] = {}
    for logical in HTTP_FUNCTIONS:
        name = physical.get(logical)
        if name is None:
            print(f"{stack} has no {logical}", file=sys.stderr)
            return 1
        config = awslambda.get_function_configuration(FunctionName=name)
        env[logical] = config.get("Environment", {}).get("Variables", {})

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "env.json").write_text(json.dumps(env, indent=2, sort_keys=True), encoding="utf-8")
    print(f"wired {', '.join(HTTP_FUNCTIONS)} against {stack}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
