"""Thin boto3 helpers. Kept separate so unit tests can monkeypatch one module."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import boto3

_s3 = None


def client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def put_json(bucket: str, key: str, payload: Any) -> str:
    client().put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload).encode("utf-8"),
        ContentType="application/json",
    )
    return key


def get_json(bucket: str, key: str) -> Any:
    body = client().get_object(Bucket=bucket, Key=key)["Body"].read()
    return json.loads(body.decode("utf-8"))


def get_text(bucket: str, key: str) -> str:
    return client().get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")


def put_file(bucket: str, key: str, path: Path, content_type: str) -> str:
    client().upload_file(str(path), bucket, key, ExtraArgs={"ContentType": content_type})
    return key


def get_file(bucket: str, key: str, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    client().download_file(bucket, key, str(path))
    return path


def put_text(bucket: str, key: str, text: str, content_type: str = "text/plain") -> str:
    client().put_object(
        Bucket=bucket, Key=key, Body=text.encode("utf-8"), ContentType=content_type
    )
    return key


def list_keys(bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    return keys


def presign(bucket: str, key: str, ttl_seconds: int) -> str:
    return client().generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=ttl_seconds
    )
