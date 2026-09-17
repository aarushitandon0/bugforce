"""DynamoDB helpers.

The resource API rejects Python floats outright, and every difficulty score in
this system is a float, so conversion happens in one place rather than at each
call site.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import boto3

_resource = None


def table(name: str):
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource.Table(name)


def to_ddb(value: Any) -> Any:
    """floats -> Decimal, recursively. Empty strings are kept (allowed since 2020)."""
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    """Decimal -> int/float, recursively, so items are JSON-serializable."""
    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if value == as_int else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    return value


def put(table_name: str, item: dict) -> None:
    table(table_name).put_item(Item=to_ddb(item))


def get(table_name: str, key: dict) -> dict | None:
    item = table(table_name).get_item(Key=key).get("Item")
    return from_ddb(item) if item else None


def dumps(payload: Any) -> str:
    return json.dumps(from_ddb(payload))
