"""Print the URL the web app should call for a stack deployed into LocalStack.

The stack's own ApiUrl output names execute-api.<region>.amazonaws.com, which
is the right answer in AWS and the wrong one here: LocalStack serves every HTTP
API from :4566 and tells them apart by subdomain. The API id is the part that
carries over.

boto3 rather than the AWS CLI, because boto3 is already a dependency of this
project and picks up AWS_ENDPOINT_URL exactly the way the handlers do.
"""
from __future__ import annotations

import sys

import boto3


def main(stack: str) -> int:
    try:
        stacks = boto3.client("cloudformation").describe_stacks(StackName=stack)
    except Exception as e:  # no stack, no endpoint, anything
        print(f"could not read stack {stack}: {e}", file=sys.stderr)
        return 1
    outputs = stacks["Stacks"][0].get("Outputs", [])
    url = next((o["OutputValue"] for o in outputs if o["OutputKey"] == "ApiUrl"), "")
    api_id = url.split("//", 1)[-1].split(".", 1)[0]
    if not api_id:
        print("stack has no ApiUrl output", file=sys.stderr)
        return 1
    print(f"http://{api_id}.execute-api.localhost.localstack.cloud:4566")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
