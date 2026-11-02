"""intel_gateway.ml_service_status (M10 §3.9): is the ML service up and what does it serve, for
the admin console. One token-protected `GET /health/ai` within 3 s, never raising, kept for a
minute."""

import asyncio
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
import structlog
from gharbasket_ml.main import create_app as create_ml_app
from gharbasket_ml.settings import Settings as MlSettings
from pydantic import SecretStr

from gharbasket.modules.intel_gateway import api, ml_status
from gharbasket.platform.settings import Settings

from ..conftest import make_settings
from .test_plan_week import TOKEN

pytestmark = pytest.mark.anyio

SYNCED: dict[str, Any] = {
    "status": "ok",
    "version": "0.1.0",
    "models": [
        {"name": "cp-sat-planner", "version": "v1", "kind": "solver", "source": "builtin"},
        {"name": "forecast", "version": "20261101", "kind": "learned", "source": "registry"},
    ],
    "osrm": "ok",
    "registry_sync": {
        "enabled": True,
        "bucket": "gharbasket-models",
        "models": {"forecast": "20261101"},
        "last_attempt_at": "2026-11-02T03:00:00+00:00",
        "last_success_at": "2026-11-02T03:00:00+00:00",
        "last_failure_at": "2026-11-01T03:00:00+00:00",
        "last_error": "R2 timed out",
    },
}


class Clock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[Clock]:
    clock = Clock()
    ml_status.reset_cache()
    original = ml_status.clock
    ml_status.clock = clock
    yield clock
    ml_status.clock = original
    ml_status.reset_cache()


def settings(**more: Any) -> Settings:
    return make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN, **more)


class Service:
    """A fake ML service answering `/health/ai`; records each request."""

    def __init__(self, answer: Any = SYNCED, status: int = 200) -> None:
        self.answer, self.status = answer, status
        self.seen: list[httpx2.Request] = []
        self.error: Exception | None = None

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        if self.error is not None:
            raise self.error
        if isinstance(self.answer, bytes):
            return httpx2.Response(self.status, content=self.answer)
        return httpx2.Response(self.status, json=self.answer)

    async def status_of(self, cfg: Settings | None = None) -> api.MlServiceStatus:
        transport = httpx2.MockTransport(self.handler)
        async with httpx2.AsyncClient(transport=transport) as http:
            return await api.ml_service_status(http, cfg or settings())


async def test_up_lists_the_served_models_and_the_registry_sync() -> None:
    service = Service()

    got = await service.status_of()

    assert got.status == "up"
    assert [(m.model, m.version) for m in got.served] == [
        ("cp-sat-planner", "v1"),
        ("forecast", "20261101"),
    ]
    assert got.registry_sync == api.RegistrySync(
        datetime(2026, 11, 2, 3, 0, tzinfo=UTC),
        datetime(2026, 11, 1, 3, 0, tzinfo=UTC),
        "R2 timed out",
    )
    (request,) = service.seen
    assert request.url == "http://ml.test/health/ai"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.extensions["timeout"]["read"] == ml_status.TIMEOUT_S == 3.0


async def test_a_service_that_does_not_sync_has_no_registry_sync() -> None:
    off = SYNCED | {"registry_sync": {"enabled": False}}
    assert (await Service(off).status_of()).registry_sync is None
    ml_status.reset_cache()
    without = {k: v for k, v in SYNCED.items() if k != "registry_sync"}
    assert (await Service(without).status_of()).registry_sync is None


async def test_without_a_url_it_is_not_configured_and_asks_nothing() -> None:
    service = Service()

    got = await service.status_of(make_settings(ml_service_url=None))

    assert (got.status, got.served, got.registry_sync) == ("not_configured", [], None)
    assert service.seen == []


@pytest.mark.parametrize(
    "fail",
    [
        httpx2.ReadTimeout("asleep"),
        httpx2.ConnectError("refused"),
        httpx2.RemoteProtocolError("reset"),
    ],
)
async def test_transport_errors_are_down_and_never_raise(fail: Exception) -> None:
    service = Service()
    service.error = fail

    with structlog.testing.capture_logs() as logs:
        got = await service.status_of()

    assert (got.status, got.served, got.registry_sync) == ("down", [], None)
    assert [(e["event"], e["reason"]) for e in logs] == [("ml_health_down", type(fail).__name__)]


@pytest.mark.parametrize(
    ("answer", "status"),
    [
        ({"detail": "bad token"}, 401),  # a token mismatch
        ({"detail": "oops"}, 503),  # a sleeping or failing host
        (b"<html>Render is waking up</html>", 200),  # not JSON
        ([1, 2], 200),  # not an object
        ({"status": "ok"}, 200),  # no models
        ({"models": [{"name": "x"}]}, 200),  # a model without a version
        ({"models": 7}, 200),
    ],
)
async def test_a_refusal_or_an_unreadable_answer_is_down(answer: Any, status: int) -> None:
    got = await Service(answer, status).status_of()

    assert got.status == "down" and got.served == []


async def test_three_seconds_is_a_hard_bound_on_the_whole_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service that trickles its answer in never trips httpx's per-phase timeouts."""
    monkeypatch.setattr(ml_status, "TIMEOUT_S", 0.05)

    async def slow(request: httpx2.Request) -> httpx2.Response:
        await asyncio.sleep(5)
        return httpx2.Response(200, json=SYNCED)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(slow)) as http:
        started = time.monotonic()
        got = await api.ml_service_status(http, settings())

    assert got.status == "down" and time.monotonic() - started < 2


async def test_the_answer_is_kept_for_a_minute_then_asked_again(fresh_cache: Clock) -> None:
    service = Service()
    first = await service.status_of()
    fresh_cache.now += ml_status.CACHE_S - 1
    assert await service.status_of() is first  # the same answer, no request
    assert len(service.seen) == 1

    service.error = httpx2.ReadTimeout("asleep")
    fresh_cache.now += 1
    later = await service.status_of()

    assert len(service.seen) == 2 and later.status == "down"
    assert await service.status_of() is later  # a down answer is kept too
    assert len(service.seen) == 2


async def test_a_changed_url_or_token_is_not_served_from_the_cache() -> None:
    service = Service()
    await service.status_of()
    await service.status_of(settings())  # same settings: cached
    assert len(service.seen) == 1

    await service.status_of(make_settings(ml_service_url="http://other.test"))

    assert len(service.seen) == 2


async def test_the_real_ml_service_answers_what_the_gateway_reads() -> None:
    """The gateway against the service's own `/health/ai`, so a change to either breaks here."""
    app = create_ml_app(MlSettings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http:
        got = await api.ml_service_status(http, settings())
        ml_status.reset_cache()
        refused = await api.ml_service_status(
            http, make_settings(ml_service_url="http://ml.test", ml_service_token="wrong-token")
        )

    assert got.status == "up" and got.registry_sync is None
    assert got.served and all(m.model and m.version for m in got.served)
    assert refused.status == "down"
