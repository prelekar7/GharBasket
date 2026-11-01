"""Following the R2 registry: the request signature, the GET-only reader, and the sync into the
local cache (first download, nothing to do, a promotion, a rollback, R2 down, a half-written
download), and that startup and `/health` never wait for R2."""

import asyncio
import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from fastapi.testclient import TestClient
from gharbasket_ml import model_sync, ranker
from gharbasket_ml.main import create_app
from gharbasket_ml.model_sync import EMPTY_SHA256, ModelSync, R2Reader, RegistryError, sign_v4
from gharbasket_ml.settings import Settings
from pydantic import SecretStr

from .test_ranker import candidates, tiny_registry

TOKEN = "ml-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
FAR = 1e18  # a deadline that never passes


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
        self.down = False
        self.break_on: str | None = None  # a key whose download writes half, then fails
        self.closed = False

    def fetch(self, key: str, dest: Path, *, max_bytes: int, deadline: float) -> bool:
        self.fetched.append(key)
        if self.down:
            raise httpx2.ConnectError("R2 is down")
        if key not in self.objects:
            return False
        body = self.objects[key]
        if key == self.break_on:
            dest.write_bytes(body[: len(body) // 2])
            raise httpx2.ReadTimeout("connection stalled")
        if len(body) > max_bytes:
            raise RegistryError(f"{key} is larger than {max_bytes} bytes")
        dest.write_bytes(body)
        return True

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
    return [f.name for f in sorted((cache / name).iterdir())] if (cache / name).exists() else []


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
    assert r2.fetch("models/a b/latest.json", tmp_path / "f", max_bytes=1 << 20, deadline=FAR)
    r2.close()
    assert (tmp_path / "f").read_bytes() == b"x" * 200_000
    (request,) = seen
    assert request.method == "GET"
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
    assert r2.fetch("missing", tmp_path / "m", max_bytes=10, deadline=FAR) is False
    assert not (tmp_path / "m").exists()
    with pytest.raises(RegistryError, match="bucket gb-mlflow does not exist"):
        r2.fetch("nobucket", tmp_path / "n", max_bytes=10, deadline=FAR)
    with pytest.raises(RegistryError, match="HTTP 403"):
        r2.fetch("denied", tmp_path / "d", max_bytes=10, deadline=FAR)
    with pytest.raises(RegistryError, match="larger than 10 bytes"):
        r2.fetch("huge", tmp_path / "h", max_bytes=10, deadline=FAR)


def test_the_reader_gives_up_at_the_deadline(tmp_path: Path) -> None:
    r2 = reader(
        httpx2.MockTransport(lambda _: httpx2.Response(200, content=b"z" * 10)), clock=lambda: 5.0
    )
    with pytest.raises(RegistryError, match="deadline"):
        r2.fetch("k", tmp_path / "k", max_bytes=100, deadline=1.0)


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
    assert sorted(f.name for f in (cache / "basket_ranker" / "v1").iterdir()) == [
        "card.json",
        "model.txt",
    ]
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

    r2.fetched.clear()
    r2.promote("v2", previous="v3")  # a rollback to a version still cached
    assert sync.sync_once() == ["basket_ranker"]
    assert r2.fetched == ["models/basket_ranker/latest.json"]
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

    assert artefacts("basket_ranker", cache) == ["latest.json", "v1"]  # no v2, no temp folder
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
    (folder / ".tmp-v1-abc").mkdir(parents=True)
    (folder / ".tmp-v1-abc" / "model.txt").write_bytes(b"half")
    (folder / ".latest.json.dead.part").write_text("{}")
    ModelSync(r2, tmp_path / "cache", bucket="gb-mlflow").sync_once()
    assert artefacts("basket_ranker", tmp_path / "cache") == ["latest.json", "v1"]


@pytest.mark.parametrize(
    ("pointer", "error"),
    [
        (b"{not json", "is not a JSON object"),
        (b"[1]", "is not a JSON object"),
        (b'{"version": "../../etc"}', "no usable version"),
        (b'{"version": ".tmp-x"}', "no usable version"),
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
    assert sorted(f.name for f in (tmp_path / "cache" / "basket_ranker" / "v1").iterdir()) == [
        "model.txt"
    ]  # the card is optional

    r2.promote("v9")
    assert sync.sync_once() == []
    assert sync.last_error == "basket_ranker: models/basket_ranker/v9/model.txt is missing"
    assert sync.status()["models"] == {"basket_ranker": "v1"}


def test_nothing_promoted_is_not_an_error(tmp_path: Path) -> None:
    sync = ModelSync(FakeR2(), tmp_path / "cache", bucket="gb-mlflow")
    assert sync.sync_once() == []
    assert sync.last_error is None and sync.status()["models"] == {"basket_ranker": None}


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


def test_startup_and_health_never_wait_for_r2(tmp_path: Path) -> None:
    gate = threading.Event()
    started = threading.Event()

    class StuckR2(FakeR2):
        def fetch(self, key: str, dest: Path, *, max_bytes: int, deadline: float) -> bool:
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
