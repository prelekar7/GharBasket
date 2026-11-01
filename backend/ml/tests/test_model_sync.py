"""Following the R2 registry: the request signature, the GET/HEAD-only reader, and the sync
into the local cache (first download, nothing to do, a promotion, a rollback, a version overwritten
in place, R2 down, a half-written download, a trickling store, a corrupt promotion), and that
startup, `/health` and shutdown never wait for R2."""

import asyncio
import hashlib
import json
import logging
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from gharbasket_ml import model_sync, ranker
from gharbasket_ml.main import _reload_ranker, create_app  # pyright: ignore[reportPrivateUsage]
from gharbasket_ml.model_sync import EMPTY_SHA256, ModelSync, R2Reader, RegistryError, sign_v4
from gharbasket_ml.settings import Settings
from pydantic import SecretStr, ValidationError

from .test_ranker import candidates, tiny_registry

TOKEN = "ml-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def never() -> bool:
    return False


def always() -> bool:
    return True


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    ranker.clear_cache()
    yield
    ranker.clear_cache()


class FakeR2:
    """An in-memory bucket behind the `Reader` interface, which can go down or break mid-file."""

    def __init__(self, objects: dict[str, bytes] | None = None) -> None:
        self.objects = dict(objects or {})
        self.fetched: list[str] = []
        self.headed: list[str] = []
        self.down = False
        self.break_on: str | None = None  # a key whose download writes half, then fails
        self.closed = False

    def fetch(
        self, key: str, dest: Path, *, max_bytes: int, expired: model_sync.Expired
    ) -> str | None:
        self.fetched.append(key)
        if self.down:
            raise httpx2.ConnectError("R2 is down")
        if key not in self.objects:
            return None
        body = self.objects[key]
        if key == self.break_on:
            dest.write_bytes(body[: len(body) // 2])
            raise httpx2.ReadTimeout("connection stalled")
        if len(body) > max_bytes:
            raise RegistryError(f"{key} is larger than {max_bytes} bytes")
        dest.write_bytes(body)
        return self._etag(body)

    def etag(self, key: str, *, expired: model_sync.Expired) -> str | None:
        self.headed.append(key)
        if self.down:
            raise httpx2.ConnectError("R2 is down")
        return self._etag(self.objects[key]) if key in self.objects else None

    @staticmethod
    def _etag(body: bytes) -> str:
        return f'"{hashlib.md5(body).hexdigest()}"'  # what S3 sends for a single-part upload

    def close(self) -> None:
        self.closed = True

    def publish(self, version: str, model: bytes, card: bytes | None = b"{}") -> None:
        base = f"models/basket_ranker/{version}"
        self.objects[f"{base}/model.txt"] = model
        if card is not None:
            self.objects[f"{base}/card.json"] = card
        self.objects[f"{base}/explanations.json"] = b"[]"  # never needed for serving

    def promote(self, version: str, previous: str | None = None) -> None:
        pointer = {"version": version, "promoted_at": "2026-11-01T00:00:00+00:00", "why": "test"}
        if previous:
            pointer["previous"] = previous
        self.objects["models/basket_ranker/latest.json"] = json.dumps(pointer).encode()


def model_bytes(tmp_path: Path, version: str) -> bytes:
    root = tiny_registry(tmp_path / f"src-{version}", version)
    return (root / "basket_ranker" / version / "model.txt").read_bytes()


def artefacts(name: str, cache: Path) -> list[str]:
    """What the ranker can see in a model's folder (the dot-entries are the sync's own)."""
    folder = cache / name
    if not folder.exists():
        return []
    return [f.name for f in sorted(folder.iterdir()) if not f.name.startswith(".")]


def files(version_dir: Path) -> list[str]:
    return sorted(f.name for f in version_dir.iterdir() if not f.name.startswith("."))


def stored(name: str, cache: Path) -> list[str]:
    store = cache / name / model_sync.STORE
    return sorted(f.name for f in store.iterdir()) if store.exists() else []


# --- the signature -------------------------------------------------------------------------


def test_the_signature_matches_the_aws_s3_example() -> None:
    """AWS's documented S3 GET example (Signature Version 4, 'Example: GET Object')."""
    headers = sign_v4(
        "GET",
        "https://examplebucket.s3.amazonaws.com/test.txt",
        {"Range": "bytes=0-9"},
        access_key="AKIAIOSFODNN7EXAMPLE",
        secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
    )
    assert headers["Authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    assert headers["x-amz-date"] == "20130524T000000Z" and headers["Range"] == "bytes=0-9"
    assert headers["x-amz-content-sha256"] == EMPTY_SHA256


def test_the_signature_matches_botocore_for_an_r2_key() -> None:
    auth = pytest.importorskip("botocore.auth")
    awsrequest = pytest.importorskip("botocore.awsrequest")
    credentials = pytest.importorskip("botocore.credentials")
    time_machine = pytest.importorskip("time_machine")
    url = "https://acc.r2.cloudflarestorage.com/gb-mlflow/models/basket_ranker/v-1_2.x/model.txt"
    now = datetime(2026, 11, 1, 4, 5, 6, tzinfo=UTC)
    with time_machine.travel(now, tick=False):
        request = awsrequest.AWSRequest(
            method="GET", url=url, headers={"x-amz-content-sha256": EMPTY_SHA256}
        )
        auth.S3SigV4Auth(credentials.Credentials("AK", "SK"), "s3", "auto").add_auth(request)
    ours = sign_v4("GET", url, {}, access_key="AK", secret_key="SK", region="auto", now=now)
    assert ours["Authorization"] == request.headers["Authorization"]


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://ACC.r2.example.com:443/b/k", "acc.r2.example.com"),
        ("http://localhost:80/b/k", "localhost"),
        ("http://localhost:8333/b/k", "localhost:8333"),
        ("https://h.example.com:8443/b/k", "h.example.com:8443"),
        ("http://[::1]:9000/b/k", "[::1]:9000"),
    ],
)
def test_the_signed_host_is_the_one_sent(url: str, host: str) -> None:
    """A default port is not part of the Host header httpx2 sends, so it is not signed either."""
    now = datetime(2026, 11, 1, tzinfo=UTC)
    seen: list[httpx2.Request] = []
    with httpx2.Client(
        transport=httpx2.MockTransport(lambda r: seen.append(r) or httpx2.Response(200))
    ) as client:
        client.get(url)
    assert seen[0].headers["host"] == host
    assert model_sync._host(url) == host  # pyright: ignore[reportPrivateUsage]
    bare = url.replace(":443/", "/").replace(":80/", "/")
    signed = sign_v4("GET", url, {}, access_key="a", secret_key="s", region="auto", now=now)
    assert signed == sign_v4(
        "GET", bare, {}, access_key="a", secret_key="s", region="auto", now=now
    )


def test_the_signature_refuses_a_query_string() -> None:
    with pytest.raises(ValueError, match="query"):
        sign_v4(
            "GET", "https://h/b/k?x=1", {}, access_key="a", secret_key="s", region="auto",
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )  # fmt: skip


# --- the reader ----------------------------------------------------------------------------


def reader(handler: httpx2.MockTransport, **kwargs: object) -> R2Reader:
    return R2Reader(
        "https://acc.r2.cloudflarestorage.com/",
        "gb-mlflow",
        "AK",
        "SK",
        transport=handler,
        now=lambda: datetime(2026, 11, 1, tzinfo=UTC),
        **kwargs,  # pyright: ignore[reportArgumentType]
    )


def test_the_reader_streams_a_signed_path_style_get(tmp_path: Path) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, content=b"x" * 200_000)

    r2 = reader(httpx2.MockTransport(handler))
    etag = r2.fetch("models/a b/latest.json", tmp_path / "f", max_bytes=1 << 20, expired=never)
    r2.close()
    assert etag == ""  # the mock sends no ETag
    assert (tmp_path / "f").read_bytes() == b"x" * 200_000
    (request,) = seen
    assert request.method == "GET" and request.headers["accept-encoding"] == "identity"
    assert str(request.url) == (
        "https://acc.r2.cloudflarestorage.com/gb-mlflow/models/a%20b/latest.json"
    )
    assert request.headers["authorization"].startswith(
        "AWS4-HMAC-SHA256 Credential=AK/20261101/auto/s3/aws4_request"
    )
    assert "SK" not in request.headers["authorization"]


def test_the_reader_tells_missing_from_broken(tmp_path: Path) -> None:
    answers = {
        "missing": httpx2.Response(404, content=b"<Error><Code>NoSuchKey</Code></Error>"),
        "nobucket": httpx2.Response(404, content=b"<Error><Code>NoSuchBucket</Code></Error>"),
        "denied": httpx2.Response(403, content=b"<Error><Code>AccessDenied</Code></Error>"),
        "huge": httpx2.Response(200, content=b"y" * 1000),
    }
    r2 = reader(httpx2.MockTransport(lambda req: answers[req.url.path.rsplit("/", 1)[1]]))
    assert r2.fetch("missing", tmp_path / "m", max_bytes=10, expired=never) is None
    assert not (tmp_path / "m").exists()
    with pytest.raises(RegistryError, match="bucket gb-mlflow does not exist"):
        r2.fetch("nobucket", tmp_path / "n", max_bytes=10, expired=never)
    with pytest.raises(RegistryError, match="HTTP 403"):
        r2.fetch("denied", tmp_path / "d", max_bytes=10, expired=never)
    with pytest.raises(RegistryError, match="larger than 10 bytes"):
        r2.fetch("huge", tmp_path / "h", max_bytes=10, expired=never)


def test_a_404_body_is_read_no_further_than_4_kib(tmp_path: Path) -> None:
    pieces: list[int] = []

    def endless() -> Iterator[bytes]:
        for i in range(10_000):
            pieces.append(i)
            yield b"<Error>" + b"x" * 1017  # 1 KiB each

    r2 = reader(httpx2.MockTransport(lambda _: httpx2.Response(404, content=endless())))
    assert r2.fetch("k", tmp_path / "k", max_bytes=10, expired=never) is None
    assert len(pieces) == 4


def test_a_trickling_store_cannot_outlast_the_deadline(tmp_path: Path) -> None:
    """Every piece is checked as it arrives, not every 64 KiB."""
    pieces: list[int] = []

    def drip() -> Iterator[bytes]:
        for i in range(6000):
            pieces.append(i)
            yield b"x" * 10

    checks = iter([False] * 6)  # the deadline passes after a few pieces

    r2 = reader(httpx2.MockTransport(lambda _: httpx2.Response(200, content=drip())))
    with pytest.raises(RegistryError, match="gave up while reading k"):
        r2.fetch("k", tmp_path / "k", max_bytes=10**8, expired=lambda: next(checks, True))
    assert len(pieces) <= 6
    with pytest.raises(RegistryError, match="gave up before reading k"):
        r2.fetch("k", tmp_path / "k", max_bytes=10**8, expired=always)
    with pytest.raises(RegistryError, match="gave up before checking k"):
        r2.etag("k", expired=always)


def test_an_encoded_body_is_refused(tmp_path: Path) -> None:
    r2 = reader(
        httpx2.MockTransport(
            lambda _: httpx2.Response(
                200, content=b"\x1f\x8b", headers={"Content-Encoding": "gzip"}
            )
        )
    )
    with pytest.raises(RegistryError, match="gzip-encoded"):
        r2.fetch("k", tmp_path / "k", max_bytes=100, expired=never)


def test_the_reader_heads_for_the_etag() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(f"{request.method} {request.url.path}")
        name = request.url.path.rsplit("/", 1)[1]
        status = {"there": 200, "gone": 404, "denied": 403}[name]
        return httpx2.Response(status, headers={"ETag": '"abc"'} if status == 200 else {})

    r2 = reader(httpx2.MockTransport(handler))
    assert r2.etag("m/there", expired=never) == '"abc"'
    assert r2.etag("m/gone", expired=never) is None
    with pytest.raises(RegistryError, match="HEAD m/denied: HTTP 403"):
        r2.etag("m/denied", expired=never)
    assert seen[0] == "HEAD /gb-mlflow/m/there"


def test_the_reader_is_built_from_the_settings_only_when_complete() -> None:
    with pytest.raises(ValueError, match="S3_ENDPOINT_URL"):
        R2Reader.from_settings(Settings(app_env="test"))
    built = R2Reader.from_settings(
        Settings(
            app_env="test",
            s3_endpoint_url="http://localhost:8333",
            s3_access_key="k",
            s3_secret_key=SecretStr("s"),
        )
    )
    assert (
        built.url("models/x/latest.json") == "http://localhost:8333/gb-mlflow/models/x/latest.json"
    )
    built.close()


# --- the sync ------------------------------------------------------------------------------


def test_the_first_sync_downloads_the_promoted_version_and_the_ranker_serves_it(
    tmp_path: Path,
) -> None:
    r2 = FakeR2()
    r2.publish("v1", model_bytes(tmp_path, "v1"))
    r2.promote("v1")
    cache = tmp_path / "cache"
    changes: list[list[str]] = []
    sync = ModelSync(r2, cache, bucket="gb-mlflow", on_change=changes.append)

    assert sync.sync_once() == ["basket_ranker"]

    assert changes == [["basket_ranker"]]
    assert artefacts("basket_ranker", cache) == ["latest.json", "v1"]
    assert files(cache / "basket_ranker" / "v1") == ["card.json", "model.txt"]
    assert (cache / "basket_ranker" / "v1").is_symlink()
    assert "models/basket_ranker/v1/explanations.json" not in r2.fetched
    answer = ranker.rank(candidates(), cache)
    assert answer.source == "ml" and answer.model == "basket_ranker-v1"
    status = sync.status()
    assert status["models"] == {"basket_ranker": "v1"} and status["last_error"] is None
    assert status["last_success_at"] is not None and status["bucket"] == "gb-mlflow"


def test_an_unchanged_pointer_downloads_nothing(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", b"model-v1")
    r2.promote("v1")
    sync = ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow")
    sync.sync_once()
    r2.fetched.clear()

    assert sync.sync_once() == []
    assert r2.fetched == ["models/basket_ranker/latest.json"]
    assert r2.headed == ["models/basket_ranker/v1/model.txt"]  # one HEAD: still the same copy


def test_a_version_overwritten_in_place_is_downloaded_again(tmp_path: Path) -> None:
    """Versions are not immutable: a rerun of the training job can overwrite one."""
    r2 = FakeR2()
    r2.publish("2026-11-01-synthetic", b"tree\nfirst run\n")
    r2.promote("2026-11-01-synthetic")
    cache = tmp_path / "cache"
    reloaded: list[list[str]] = []
    sync = ModelSync(r2, cache, bucket="gb-mlflow", on_change=reloaded.append)
    sync.sync_once()
    first = stored("basket_ranker", cache)

    retrained = b"tree\nretrained the same day\n"
    r2.publish("2026-11-01-synthetic", retrained)
    assert sync.sync_once() == ["basket_ranker"]

    served = cache / "basket_ranker" / "2026-11-01-synthetic"
    assert (served / "model.txt").read_bytes() == retrained
    assert reloaded == [["basket_ranker"], ["basket_ranker"]]
    assert len(stored("basket_ranker", cache)) == 1 and stored("basket_ranker", cache) != first
    r2.fetched.clear()
    assert sync.sync_once() == []  # and then it is current again
    assert r2.fetched == ["models/basket_ranker/latest.json"]


def test_a_promotion_swaps_the_new_version_in_and_keeps_the_previous(tmp_path: Path) -> None:
    r2 = FakeR2()
    for version in ("v1", "v2", "v3"):
        r2.publish(version, model_bytes(tmp_path, version))
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow")
    r2.promote("v1")
    sync.sync_once()
    assert ranker.rank(candidates(), cache).model == "basket_ranker-v1"

    r2.promote("v2", previous="v1")
    assert sync.sync_once() == ["basket_ranker"]
    assert artefacts("basket_ranker", cache) == ["latest.json", "v1", "v2"]
    ranker.clear_cache()
    assert ranker.rank(candidates(), cache).model == "basket_ranker-v2"

    r2.promote("v3", previous="v2")
    sync.sync_once()
    assert artefacts("basket_ranker", cache) == ["latest.json", "v2", "v3"]  # v1 pruned

    assert len(stored("basket_ranker", cache)) == 2  # v1's files went with its link

    r2.fetched.clear()
    r2.headed.clear()
    r2.promote("v2", previous="v3")  # a rollback to a version still cached
    assert sync.sync_once() == ["basket_ranker"]
    assert r2.fetched == ["models/basket_ranker/latest.json"]
    assert r2.headed == ["models/basket_ranker/v2/model.txt"]
    assert sync.status()["models"] == {"basket_ranker": "v2"}


def test_r2_down_keeps_what_is_served(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", model_bytes(tmp_path, "v1"))
    r2.promote("v1")
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow")
    sync.sync_once()

    r2.down = True
    assert sync.sync_once() == []

    status = sync.status()
    assert status["last_error"] == "basket_ranker: ConnectError"
    assert status["last_failure_at"] is not None and status["models"] == {"basket_ranker": "v1"}
    assert ranker.rank(candidates(), cache).source == "ml"
    assert "SK" not in json.dumps(status)


def test_r2_down_from_the_start_leaves_the_fallback(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.down = True
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow")
    assert sync.sync_once() == []
    assert sync.status()["last_success_at"] is None
    assert ranker.rank(candidates(), cache).source == "fallback"


def test_a_half_written_download_is_never_served(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", model_bytes(tmp_path, "v1"))
    r2.publish("v2", model_bytes(tmp_path, "v2"))
    r2.promote("v1")
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow")
    sync.sync_once()

    r2.promote("v2", previous="v1")
    r2.break_on = "models/basket_ranker/v2/model.txt"
    assert sync.sync_once() == []

    assert artefacts("basket_ranker", cache) == ["latest.json", "v1"]  # no v2
    assert len(stored("basket_ranker", cache)) == 1  # and no half-written copy
    assert sync.status()["models"] == {"basket_ranker": "v1"}
    assert sync.status()["last_error"] == "basket_ranker: ReadTimeout"
    ranker.clear_cache()
    assert ranker.rank(candidates(), cache).model == "basket_ranker-v1"

    r2.break_on = None  # the next sync completes the promotion
    assert sync.sync_once() == ["basket_ranker"]
    assert sync.status()["models"] == {"basket_ranker": "v2"}


def test_leftovers_of_an_interrupted_sync_are_removed(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", b"m")
    r2.promote("v1")
    folder = tmp_path / "cache" / "basket_ranker"
    (folder / ".v" / "v1.half").mkdir(parents=True)
    (folder / ".v" / "v1.half" / "model.txt").write_bytes(b"half")
    (folder / ".latest.json.dead.part").write_text("{}")
    (folder / ".link-dead").symlink_to(".v/v1.half")
    ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow").sync_once()
    assert artefacts("basket_ranker", tmp_path / "cache") == ["latest.json", "v1"]
    assert sorted(f.name for f in folder.iterdir()) == [".v", "latest.json", "v1"]
    assert stored("basket_ranker", tmp_path / "cache") != ["v1.half"]
    assert len(stored("basket_ranker", tmp_path / "cache")) == 1


def test_a_folder_named_like_a_version_is_replaced_not_served(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", b"real")
    r2.promote("v1")
    junk = tmp_path / "cache" / "basket_ranker" / "v1"
    junk.mkdir(parents=True)
    (junk / "model.txt").write_bytes(b"stale")
    ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow").sync_once()
    assert junk.is_symlink() and (junk / "model.txt").read_bytes() == b"real"


def test_the_pointer_name_is_never_a_version(tmp_path: Path) -> None:
    """`latest.json` as a version would make the pointer a folder and wedge the cache."""
    r2 = FakeR2()
    r2.publish("latest.json", b"m")
    r2.publish("v1", b"m")
    r2.promote("latest.json")
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow")
    assert sync.sync_once() == []
    assert "no usable version: 'latest.json'" in str(sync.last_error)

    (cache / "basket_ranker" / "latest.json").mkdir()  # as an older build could have left it
    r2.promote("v1")
    assert sync.sync_once() == ["basket_ranker"]
    assert sync.status()["models"] == {"basket_ranker": "v1"}


@pytest.mark.parametrize(
    ("pointer", "error"),
    [
        (b"{not json", "is not a JSON object"),
        (b"[1]", "is not a JSON object"),
        (b'{"version": "../../etc"}', "no usable version"),
        (b'{"version": ".tmp-x"}', "no usable version"),
        (b'{"version": "latest.json"}', "no usable version"),
        (b'{"version": "v1.JSON"}', "no usable version"),
        (b'{"version": 7}', "no usable version"),
    ],
)
def test_a_bad_pointer_moves_nothing(tmp_path: Path, pointer: bytes, error: str) -> None:
    r2 = FakeR2({"models/basket_ranker/latest.json": pointer})
    sync = ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow")
    assert sync.sync_once() == []
    assert error in str(sync.last_error)
    assert artefacts("basket_ranker", tmp_path / "cache") == []


def test_a_pointer_to_a_missing_model_moves_nothing(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", b"m", card=None)
    r2.promote("v1")
    sync = ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow")
    sync.sync_once()
    assert files(tmp_path / "cache" / "basket_ranker" / "v1") == ["model.txt"]  # card optional

    r2.promote("v9")
    assert sync.sync_once() == []
    assert sync.last_error == "basket_ranker: models/basket_ranker/v9/model.txt is missing"
    assert sync.status()["models"] == {"basket_ranker": "v1"}


def test_nothing_promoted_is_not_an_error(tmp_path: Path) -> None:
    sync = ModelSync(FakeR2(), tmp_path / "cache", bucket="gb-mlflow")
    assert sync.sync_once() == []
    assert sync.last_error is None and sync.status()["models"] == {"basket_ranker": None}


def test_a_corrupt_promotion_keeps_the_last_good_model(tmp_path: Path) -> None:
    """Through the service's own reload after a sync, not a manual clear_cache()."""
    r2 = FakeR2()
    r2.publish("v1", model_bytes(tmp_path, "v1"))
    r2.publish("v2", b"not a lightgbm model")
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow", on_change=_reload_ranker(cache, 600.0))
    r2.promote("v1")
    sync.sync_once()
    assert ranker.rank(candidates(), cache, 600.0).model == "basket_ranker-v1"

    r2.promote("v2", previous="v1")
    assert sync.sync_once() == ["basket_ranker"]

    answer = ranker.rank(candidates(), cache, 600.0)
    assert answer.source == "ml" and answer.model == "basket_ranker-v1"


def test_a_good_promotion_is_served_at_once(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", model_bytes(tmp_path, "v1"))
    r2.publish("v2", model_bytes(tmp_path, "v2"))
    cache = tmp_path / "cache"
    sync = ModelSync(r2, cache, bucket="gb-mlflow", on_change=_reload_ranker(cache, 600.0))
    assert ranker.rank(candidates(), cache, 600.0).source == "fallback"  # before any sync
    r2.promote("v1")
    sync.sync_once()
    assert ranker.rank(candidates(), cache, 600.0).model == "basket_ranker-v1"
    r2.promote("v2", previous="v1")
    sync.sync_once()
    assert ranker.rank(candidates(), cache, 600.0).model == "basket_ranker-v2"


def test_a_failing_reload_does_not_fail_the_sync(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v1", b"m")
    r2.promote("v1")

    def explode(_: list[str]) -> None:
        raise RuntimeError("boom")

    sync = ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow", on_change=explode)
    assert sync.sync_once() == ["basket_ranker"]
    assert sync.last_error is None


def test_run_forever_keeps_going_after_an_error(tmp_path: Path) -> None:
    calls: list[int] = []
    sync = ModelSync(FakeR2(), tmp_path / "cache", bucket="gb-mlflow")
    third = threading.Event()

    def flaky() -> list[str]:
        calls.append(1)
        if len(calls) == 3:
            third.set()
        if len(calls) == 1:
            raise RuntimeError("unexpected")
        return []

    sync.sync_once = flaky  # pyright: ignore[reportAttributeAccessIssue]

    async def run() -> None:
        task = asyncio.create_task(sync.run_forever(0.001))
        assert await asyncio.to_thread(third.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert len(calls) >= 3


# --- the service ---------------------------------------------------------------------------


def r2_settings(tmp_path: Path, **extra: object) -> Settings:
    return Settings(
        app_env="test",
        ml_service_token=SecretStr(TOKEN),
        s3_endpoint_url="https://acc.r2.cloudflarestorage.com",
        s3_access_key="AK",
        s3_secret_key=SecretStr("very-secret-key"),
        models_cache_dir=tmp_path / "cache",
        **extra,  # pyright: ignore[reportArgumentType]
    )


def test_the_settings_choose_the_models_folder(tmp_path: Path) -> None:
    assert Settings(app_env="test").served_models_dir is None
    assert not Settings(app_env="test", s3_endpoint_url="http://x").registry_sync_enabled
    synced = r2_settings(tmp_path)
    assert synced.registry_sync_enabled and synced.served_models_dir == tmp_path / "cache"
    explicit = r2_settings(tmp_path, models_dir=tmp_path / "mine")
    assert not explicit.registry_sync_enabled
    assert explicit.served_models_dir == tmp_path / "mine"


def test_a_half_set_registry_is_warned_about_once_at_startup(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert Settings(app_env="test").registry_sync_warning is None
    assert r2_settings(tmp_path).registry_sync_warning is None
    partial = Settings(app_env="test", s3_endpoint_url="http://x", s3_access_key="k")
    assert partial.registry_sync_warning == (
        "only S3_ENDPOINT_URL, S3_ACCESS_KEY of the three S3 settings set: R2 is not read"
    )
    both = r2_settings(tmp_path, models_dir=tmp_path / "mine")
    assert str(both.registry_sync_warning).startswith("MODELS_DIR is set, so S3_ENDPOINT_URL")
    with (
        caplog.at_level(logging.WARNING, logger="gharbasket_ml.main"),
        TestClient(create_app(partial)),
    ):
        pass
    assert [r.getMessage() for r in caplog.records] == [
        f"model registry: {partial.registry_sync_warning}"
    ]


def test_the_endpoint_must_be_an_http_url() -> None:
    with pytest.raises(ValidationError, match="http:// or https://"):
        Settings(app_env="test", s3_endpoint_url="acc.r2.cloudflarestorage.com")
    assert Settings(app_env="test", s3_endpoint_url="").s3_endpoint_url is None


def test_startup_and_health_never_wait_for_r2(tmp_path: Path) -> None:
    gate = threading.Event()
    started = threading.Event()

    class StuckR2(FakeR2):
        def fetch(
            self, key: str, dest: Path, *, max_bytes: int, expired: model_sync.Expired
        ) -> str | None:
            started.set()
            gate.wait(10)
            raise httpx2.ConnectTimeout("R2 did not answer")

    stuck = StuckR2()
    app = create_app(r2_settings(tmp_path))
    app.state.registry_reader = stuck
    with TestClient(app) as client:
        assert started.wait(5)  # the sync runs, and is stuck
        assert client.get("/health").status_code == 200
        ranked = client.post("/basket/rank", json=candidates().model_dump(), headers=AUTH)
        assert ranked.status_code == 200 and ranked.json()["source"] == "fallback"
        sync_status = client.get("/health/ai", headers=AUTH).json()["registry_sync"]
        assert sync_status["enabled"] is True and sync_status["last_success_at"] is None
        assert "very-secret-key" not in json.dumps(sync_status)
        gate.set()
    assert stuck.closed


def test_shutdown_stops_the_sync_and_closes_the_client_after_it(tmp_path: Path) -> None:
    started = threading.Event()
    events: list[str] = []

    class TricklingR2(FakeR2):
        def fetch(
            self, key: str, dest: Path, *, max_bytes: int, expired: model_sync.Expired
        ) -> str | None:
            started.set()
            while not expired():  # a store that never finishes, but each piece is checked
                time.sleep(0.005)
            events.append("sync gave up")
            raise RegistryError("the sync gave up")

        def close(self) -> None:
            events.append("client closed")

    app = create_app(r2_settings(tmp_path))
    app.state.registry_reader = TricklingR2()
    with TestClient(app):
        assert started.wait(5)
    assert events == ["sync gave up", "client closed"]


def test_the_service_serves_what_it_synced(tmp_path: Path) -> None:
    r2 = FakeR2()
    r2.publish("v7", model_bytes(tmp_path, "v7"))
    r2.promote("v7")
    app = create_app(r2_settings(tmp_path))
    app.state.registry_reader = r2
    with TestClient(app) as client:
        status: dict[str, object] = {}
        for _ in range(500):  # the first sync runs in the background
            status = client.get("/health/ai", headers=AUTH).json()["registry_sync"]
            if status["last_success_at"]:
                break
            threading.Event().wait(0.01)
        assert status["models"] == {"basket_ranker": "v7"} and status["last_error"] is None
        ranked = client.post("/basket/rank", json=candidates().model_dump(), headers=AUTH)
        assert ranked.json()["source"] == "ml" and ranked.json()["model"] == "basket_ranker-v7"
        models = client.get("/models", headers=AUTH).json()
        assert {"name": "basket_ranker", "version": "v7"}.items() <= next(
            m for m in models if m["name"] == "basket_ranker"
        ).items()


def test_without_the_s3_settings_nothing_is_synced() -> None:
    with TestClient(create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))) as c:
        assert c.get("/health/ai", headers=AUTH).json()["registry_sync"] == {"enabled": False}


def test_the_served_models_are_what_serving_reads() -> None:
    assert dict(model_sync.SERVED) == {
        "basket_ranker": model_sync.Artefacts(("model.txt",), ("card.json",))
    }
