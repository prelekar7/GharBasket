"""The gateway's circuit breaker and its job-time timeout: a sleeping or failing ML service costs
a customer one wait, not one wait a call; a scheduled job waits longer and ignores the breaker."""

import json
from typing import Any

import httpx2
import pytest
import structlog

from gharbasket.modules.intel_gateway import api
from gharbasket.modules.intel_gateway.breaker import CircuitBreaker, ml_breaker
from gharbasket.modules.intel_gateway.schemas import (
    PantryItem,
    PantryRequest,
    PlanRequest,
    RankResponse,
)
from gharbasket.platform.settings import Settings

from ..conftest import make_settings
from .test_plan_week import TOKEN, plan_request
from .test_rank_basket import candidates

pytestmark = pytest.mark.anyio

COOLDOWN_S = 60.0


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def settings(**more: Any) -> Settings:
    return make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN, **more)


class Service:
    """A fake ML service: `up` answers /basket/rank, otherwise it times out; records each call."""

    def __init__(self) -> None:
        self.up = False
        self.seen: list[httpx2.Request] = []
        self.timeouts: list[float] = []

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.seen.append(request)
        self.timeouts.append(request.extensions["timeout"]["read"])
        if not self.up:
            raise httpx2.ReadTimeout("asleep", request=request)
        body = json.loads(request.content)
        top = body["candidates"][0]["product"]
        return httpx2.Response(
            200,
            json={
                "source": "ml",
                "model": "m",
                "ranked": [{"product": top, "score": 1, "rank": 1}],
            },
        )


async def rank(service: Service, cfg: Settings | None = None, *, job: bool = False) -> RankResponse:
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(service.handler)) as http:
        return await api.rank_basket(http, cfg or settings(), candidates(), job=job)


# --- the breaker alone ----------------------------------------------------------------------------


def test_the_breaker_opens_after_n_failures_probes_once_then_closes() -> None:
    clock = Clock()
    breaker = CircuitBreaker(clock)

    for _ in range(2):
        assert breaker.allow(3, COOLDOWN_S)
        breaker.record_failure(3)
    assert breaker.state(COOLDOWN_S) == "closed"
    breaker.record_failure(3)  # the third in a row
    assert breaker.state(COOLDOWN_S) == "open"
    assert not breaker.allow(3, COOLDOWN_S)

    clock.now += COOLDOWN_S - 1
    assert not breaker.allow(3, COOLDOWN_S)
    clock.now += 1
    assert breaker.state(COOLDOWN_S) == "half_open"
    assert breaker.allow(3, COOLDOWN_S)  # the probe
    assert not breaker.allow(3, COOLDOWN_S)  # others wait for the probe's answer

    breaker.record_success()
    assert breaker.state(COOLDOWN_S) == "closed"
    assert breaker.allow(3, COOLDOWN_S)


def test_a_failed_probe_opens_it_for_another_cooldown_and_a_lost_probe_is_replaced() -> None:
    clock = Clock()
    breaker = CircuitBreaker(clock)
    for _ in range(3):
        breaker.record_failure(3)
    clock.now += COOLDOWN_S
    assert breaker.allow(3, COOLDOWN_S)
    breaker.record_failure(3)  # the probe failed
    assert not breaker.allow(3, COOLDOWN_S)
    clock.now += COOLDOWN_S
    assert breaker.allow(3, COOLDOWN_S)  # probe 2 is never reported (its task was cancelled)
    assert not breaker.allow(3, COOLDOWN_S)
    clock.now += COOLDOWN_S
    assert breaker.allow(3, COOLDOWN_S)


def test_a_success_resets_the_run_of_failures_and_threshold_zero_turns_it_off() -> None:
    breaker = CircuitBreaker(Clock())
    breaker.record_failure(3)
    breaker.record_failure(3)
    breaker.record_success()
    breaker.record_failure(3)
    breaker.record_failure(3)
    assert breaker.state(COOLDOWN_S) == "closed"

    for _ in range(10):
        breaker.record_failure(0)
    assert breaker.allow(0, COOLDOWN_S) and breaker.state(COOLDOWN_S) == "closed"


# --- through the gateway --------------------------------------------------------------------------


async def test_three_failed_calls_open_the_breaker_and_the_fourth_never_leaves() -> None:
    service = Service()
    with structlog.testing.capture_logs() as logs:
        answers = [await rank(service) for _ in range(5)]

    assert all(a.source == "fallback" for a in answers)  # the accounting is unchanged
    assert len(service.seen) == 3
    reasons = [e["reason"] for e in logs if e["event"] == "ml_fallback"]
    assert reasons == ["ReadTimeout"] * 3 + ["circuit_open"] * 2


async def test_after_the_cooldown_one_probe_goes_and_its_success_closes_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    monkeypatch.setattr(ml_breaker, "_clock", clock)
    service = Service()
    for _ in range(4):
        await rank(service)
    assert len(service.seen) == 3 and ml_breaker.state(COOLDOWN_S) == "open"

    service.up = True
    await rank(service)
    assert len(service.seen) == 3  # still cooling down: the service is not even asked

    clock.now += COOLDOWN_S
    probe = await rank(service)
    assert probe.source == "ml" and len(service.seen) == 4
    assert ml_breaker.state(COOLDOWN_S) == "closed"
    assert (await rank(service)).source == "ml" and len(service.seen) == 5


async def test_a_failed_probe_keeps_the_breaker_open(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(ml_breaker, "_clock", clock)
    service = Service()
    for _ in range(3):
        await rank(service)
    clock.now += COOLDOWN_S
    await rank(service)  # the probe, timing out again
    assert len(service.seen) == 4
    await rank(service)
    assert len(service.seen) == 4 and ml_breaker.state(COOLDOWN_S) == "open"


@pytest.mark.parametrize(
    ("response", "opens"),
    [
        (httpx2.Response(503), True),
        (httpx2.Response(422, json={}), False),
        (httpx2.Response(200, json={"bad": 1}), False),
    ],
)
async def test_only_an_unreachable_service_counts_as_a_failure(
    response: httpx2.Response, opens: bool
) -> None:
    calls = 0

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        return response

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        for _ in range(4):
            assert (await api.rank_basket(http, settings(), candidates())).source == "fallback"
    assert calls == (3 if opens else 4)


async def test_threshold_zero_never_opens_the_breaker() -> None:
    service = Service()
    for _ in range(6):
        await rank(service, settings(ml_breaker_threshold=0))
    assert len(service.seen) == 6


async def test_an_unconfigured_service_does_not_touch_the_breaker() -> None:
    async with httpx2.AsyncClient() as http:
        for _ in range(5):
            await api.rank_basket(http, make_settings(), candidates())
    assert ml_breaker.state(COOLDOWN_S) == "closed"


async def test_a_job_call_goes_through_an_open_breaker_and_its_success_closes_it() -> None:
    service = Service()
    for _ in range(3):
        await rank(service)
    assert ml_breaker.state(COOLDOWN_S) == "open"

    service.up = True
    assert (await rank(service)).source == "fallback"  # a customer is short-circuited
    answered = await rank(service, job=True)  # the Sunday job is not

    assert answered.source == "ml"
    assert ml_breaker.state(COOLDOWN_S) == "closed"
    assert (await rank(service)).source == "ml"


async def test_a_failing_job_call_keeps_the_breaker_open() -> None:
    service = Service()
    for _ in range(3):
        await rank(service)
    assert (await rank(service, job=True)).source == "fallback"
    assert len(service.seen) == 4 and ml_breaker.state(COOLDOWN_S) == "open"


# --- the job-time timeout -------------------------------------------------------------------------


async def test_a_job_waits_ml_job_timeout_s_and_a_customer_ml_timeout_s() -> None:
    service = Service()
    cfg = settings(ml_timeout_s=3.0, ml_job_timeout_s=30.0)

    await rank(service, cfg)
    await rank(service, cfg, job=True)

    assert service.timeouts == [3.0, 30.0]


async def test_the_planner_and_the_pantry_twin_have_the_job_timeout_too() -> None:
    seen: dict[str, tuple[float, dict[str, Any]]] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen[request.url.path + ("/job" if request.extensions["timeout"]["read"] > 3 else "")] = (
            request.extensions["timeout"]["read"],
            json.loads(request.content),
        )
        return httpx2.Response(500)

    pantry = PantryRequest(items=[PantryItem(product="milk", step=500, prior_mean=1400)])
    cfg = settings(ml_breaker_threshold=0)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        await api.plan_week(http, cfg, plan_request())
        await api.plan_week(http, cfg, plan_request(), job=True)
        await api.pantry_quantities(http, cfg, pantry)
        await api.pantry_quantities(http, cfg, pantry, job=True)

    assert set(seen) == {
        "/plan/week",
        "/plan/week/job",
        "/pantry/quantities",
        "/pantry/quantities/job",
    }
    assert seen["/plan/week"][0] == 3.0 and seen["/plan/week/job"][0] == 30.0
    assert seen["/pantry/quantities/job"][0] == 30.0


@pytest.mark.parametrize(
    ("job_timeout_s", "job", "sent"),
    [
        (30.0, False, 1.0),  # a preview keeps the request's limit
        (30.0, True, 5.0),  # a job: half of 30 s, up to the planner's own 5 s ceiling
        (8.0, True, 4.0),  # the solver's share scales with the job timeout
        (1.0, True, 0.5),
    ],
)
async def test_the_solver_share_scales_with_the_job_timeout(
    job_timeout_s: float, job: bool, sent: float
) -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(500)

    cfg = settings(ml_job_timeout_s=job_timeout_s)
    req: PlanRequest = plan_request()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        await api.plan_week(http, cfg, req, job=job)

    assert seen[0]["time_limit_s"] == sent
