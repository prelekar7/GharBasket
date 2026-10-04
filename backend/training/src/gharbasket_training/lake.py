"""The lake (ADR-0025): Parquet files under `s3://gb-lake/...`, or a local folder for tests.

Paths look like `external/mandi/date=2026-10-04/maharashtra.parquet`. A `Lake` reads and writes
whole objects; jobs keep their data small enough for that.
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

    def keys(self, prefix: str) -> Iterator[str]:
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Contents", []):
                if item["Key"].endswith(".parquet"):
                    yield item["Key"]


def open_lake(settings: Settings, local: Path | None) -> Lake:
    """A local folder when given (laptops, tests), else the S3 bucket from the settings."""
    if local is not None:
        return LocalLake(local)
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
    return S3Lake(client, settings.s3_bucket_lake)
