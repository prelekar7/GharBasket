"""The lake (ADR-0025): Parquet files under `s3://gb-lake/...`, or a local folder for tests.

Paths look like `external/mandi/date=2026-10-04/maharashtra.parquet`. A `Lake` reads and writes
whole objects; jobs keep their data small enough for that. The model registry
(`s3://gb-mlflow/models/...`) is a `Registry` over the same interface.
"""

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from gharbasket_training.settings import Settings


class Lake(Protocol):
    def write(self, key: str, table: pa.Table) -> None: ...
    def read(self, key: str) -> pa.Table | None: ...
    def keys(self, prefix: str) -> Iterator[str]: ...
    def write_bytes(self, key: str, body: bytes) -> None: ...
    def read_bytes(self, key: str) -> bytes | None: ...


@dataclass(frozen=True)
class LocalLake:
    root: Path

    def write(self, key: str, table: pa.Table) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)

    def read(self, key: str) -> pa.Table | None:
        path = self.root / key
        return pq.read_table(path) if path.exists() else None

    def keys(self, prefix: str) -> Iterator[str]:
        base = self.root / prefix
        if not base.exists():
            return iter(())
        return (str(p.relative_to(self.root)) for p in sorted(base.rglob("*.parquet")))

    def write_bytes(self, key: str, body: bytes) -> None:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)

    def read_bytes(self, key: str) -> bytes | None:
        path = self.root / key
        return path.read_bytes() if path.exists() else None


@dataclass(frozen=True)
class S3Lake:
    client: Any  # a boto3 S3 client
    bucket: str

    def write(self, key: str, table: pa.Table) -> None:
        buffer = io.BytesIO()
        pq.write_table(table, buffer)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=buffer.getvalue())

    def read(self, key: str) -> pa.Table | None:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None
        return pq.read_table(io.BytesIO(body))

    def write_bytes(self, key: str, body: bytes) -> None:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=body)

    def read_bytes(self, key: str) -> bytes | None:
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def keys(self, prefix: str) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                if item["Key"].endswith(".parquet"):
                    yield item["Key"]


def _s3(settings: Settings, bucket: str) -> S3Lake:
    if not (settings.s3_endpoint_url and settings.s3_access_key and settings.s3_secret_key):
        raise RuntimeError("set S3_ENDPOINT_URL, S3_ACCESS_KEY and S3_SECRET_KEY, or pass --out")
    import boto3  # heavy; only when the bucket is used

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
        region_name=settings.s3_region,
    )
    return S3Lake(client, bucket)


def open_lake(settings: Settings, local: Path | None) -> Lake:
    """A local folder when given (laptops, tests), else the S3 bucket from the settings."""
    if local is not None:
        return LocalLake(local)
    return _s3(settings, settings.s3_bucket_lake)


@dataclass(frozen=True)
class Registry:
    """The model registry (ADR-0025): `s3://gb-mlflow/models/<name>/<version>/` with a
    `<name>/latest.json` pointer, or a local folder with the layout the Space's `MODELS_DIR`
    expects (`<name>/latest.json`, `<name>/<version>/model.txt`)."""

    lake: Lake
    prefix: str  # "models/" in the bucket, "" in a local MODELS_DIR

    def key(self, name: str, *parts: str) -> str:
        return "/".join((f"{self.prefix}{name}", *parts))


def open_registry(settings: Settings, local: Path | None) -> Registry:
    """A local `MODELS_DIR` when given, else `models/` in the registry bucket (`gb-mlflow`)."""
    if local is not None:
        return Registry(LocalLake(local), "")
    return Registry(_s3(settings, settings.s3_bucket_mlflow), "models/")
