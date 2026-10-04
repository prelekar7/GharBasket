from pathlib import Path

import pyarrow as pa
import pytest
from gharbasket_training.lake import LocalLake, S3Lake, open_lake
from gharbasket_training.settings import Settings
from pydantic import SecretStr


def test_the_local_lake_round_trips_and_lists_parquet_keys(tmp_path: Path) -> None:
    lake = LocalLake(tmp_path)
    table = pa.table({"a": [1, 2]})

    lake.write("external/mandi/date=2026-10-04/maharashtra.parquet", table)
    lake.write("external/mandi/date=2026-10-03/maharashtra.parquet", table)

    assert list(lake.keys("external/mandi/")) == [
        "external/mandi/date=2026-10-03/maharashtra.parquet",
        "external/mandi/date=2026-10-04/maharashtra.parquet",
    ]
    assert lake.read("external/mandi/date=2026-10-04/maharashtra.parquet") == table
    assert lake.read("nope.parquet") is None
    assert list(lake.keys("nothing/")) == []


class FakeS3:
    """Just enough of a boto3 client: objects in a dict, paginated listing."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> None:
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict:
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey
        import io

        return {"Body": io.BytesIO(self.objects[Key])}

    def get_paginator(self, name: str) -> "FakeS3":
        return self

    def paginate(self, Bucket: str, Prefix: str) -> list[dict]:
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return [
            {"Contents": [{"Key": k} for k in keys[:1]]},
            {"Contents": [{"Key": k} for k in keys[1:]]},
        ]


def test_the_s3_lake_uses_the_bucket_and_skips_non_parquet_keys() -> None:
    client = FakeS3()
    lake = S3Lake(client, "gb-lake")
    table = pa.table({"a": [1]})

    lake.write("x/date=1/a.parquet", table)
    lake.write("x/date=2/b.parquet", table)
    client.objects["x/notes.txt"] = b"hi"

    assert list(lake.keys("x/")) == ["x/date=1/a.parquet", "x/date=2/b.parquet"]
    assert lake.read("x/date=1/a.parquet") == table
    assert lake.read("x/missing.parquet") is None


def test_open_lake_prefers_a_local_folder_and_needs_s3_settings_otherwise(tmp_path: Path) -> None:
    assert isinstance(open_lake(Settings(), tmp_path), LocalLake)
    with pytest.raises(RuntimeError, match="S3_ENDPOINT_URL"):
        open_lake(Settings(), None)
    lake = open_lake(
        Settings(
            s3_endpoint_url="http://localhost:8333", s3_access_key="a", s3_secret_key=SecretStr("b")
        ),
        None,
    )
    assert isinstance(lake, S3Lake) and lake.bucket == "gb-lake"
