"""The gateway's circuit breakers and its job-time timeout: a sleeping or failing ML service costs
a customer one wait, not one wait a call, and only on the path that fails; a scheduled job waits
longer, may probe an open breaker once a cool-down, and has a budget for the run."""

import json
from typing import Any

import httpx2
import pytest
import structlog

from gharbasket.modules.intel_gateway import api
from gharbasket.modules.intel_gateway.breaker import CircuitBreaker, JobBudget, ml_breakers
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
from .test_slot_prices import three_stops, x

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


async def rank(
    service: Service, cfg: Settings | None = None, *, job: JobBudget | None = None
) -> RankResponse:
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


def test_a_job_probes_an_open_breaker_once_a_cooldown_and_the_rest_fall_back() -> None:
    clock = Clock()
    breaker = CircuitBreaker(clock)
    assert breaker.allow_job(3, COOLDOWN_S)  # closed: free
    for _ in range(3):
        breaker.record_failure(3)

    clock.now += 5  # well inside the cool-down: a customer waits, a job may probe
    assert not breaker.allow(3, COOLDOWN_S)
    assert breaker.allow_job(3, COOLDOWN_S)
    assert not breaker.allow_job(3, COOLDOWN_S)  # everyone else falls back at once
    breaker.record_failure(3)  # the job's probe failed
    clock.now += COOLDOWN_S - 1
    assert not breaker.allow_job(3, COOLDOWN_S)  # not again within the cool-down
    clock.now += 1
    assert breaker.allow_job(3, COOLDOWN_S)

    breaker.record_success()
    assert breaker.allow_job(3, COOLDOWN_S) and breaker.allow_job(3, COOLDOWN_S)
    assert breaker.allow_job(0, COOLDOWN_S)


def test_a_job_budget_is_spent_by_the_calls_charged_to_it() -> None:
    clock = Clock()
    budget = JobBudget(20.0, clock)
    started = budget.now()
    clock.now += 12
    budget.charge(started)
    assert not budget.exhausted and budget.spent_s == 12
    started = budget.now()
    clock.now += 8
    budget.charge(started)
    assert budget.exhausted


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
    monkeypatch.setattr(ml_breakers, "clock", clock)
    service = Service()
    for _ in range(4):
        await rank(service)
    assert len(service.seen) == 3 and ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "open"

    service.up = True
    await rank(service)
    assert len(service.seen) == 3  # still cooling down: the service is not even asked

    clock.now += COOLDOWN_S
    probe = await rank(service)
    assert probe.source == "ml" and len(service.seen) == 4
    assert ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "closed"
    assert (await rank(service)).source == "ml" and len(service.seen) == 5


async def test_a_failed_probe_keeps_the_breaker_open(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(ml_breakers, "clock", clock)
    service = Service()
    for _ in range(3):
        await rank(service)
    clock.now += COOLDOWN_S
    await rank(service)  # the probe, timing out again
    assert len(service.seen) == 4
    await rank(service)
    assert len(service.seen) == 4 and ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "open"


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
    assert ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "closed"


async def test_a_job_probes_an_open_breaker_once_and_its_success_closes_it() -> None:
    service = Service()
    for _ in range(3):
        await rank(service)
    assert ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "open"

    service.up = True
    assert (await rank(service)).source == "fallback"  # a customer is short-circuited
    run = JobBudget(20.0)
    answered = await rank(service, job=run)  # the job's probe is not, inside the cool-down

    assert answered.source == "ml"
    assert ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "closed"
    assert (await rank(service)).source == "ml"


async def test_a_hung_service_costs_a_job_one_probe_a_cooldown_not_one_wait_a_household() -> None:
    service = Service()
    # Eight runs (each its own budget, so none knows the service is down yet): three calls open
    # the breaker (each a full job wait on a real hung service), one probe may follow; the other
    # four fall back at once.
    answers = [await rank(service, job=JobBudget(1000.0)) for _ in range(8)]

    assert all(a.source == "fallback" for a in answers)
    assert len(service.seen) == 4
    assert service.timeouts == [30.0] * 4


async def test_an_open_breaker_on_one_path_does_not_stop_another() -> None:
    """Staging's preview /plan/week never fits its timeout; green-slot quotes are fast."""
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        if request.url.path == "/plan/week":
            raise httpx2.ReadTimeout("planner too slow", request=request)
        return httpx2.Response(200, json={"not": "a quote"})  # up, but not a quote: the fallback

    cfg = settings()
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        for _ in range(5):
            await api.plan_week(http, cfg, plan_request())
        assert seen == ["/plan/week"] * 3  # open after three
        assert ml_breakers.get("/plan/week").state(COOLDOWN_S) == "open"
        await api.price_slots(http, cfg, three_stops(), x())  # still called
        await api.rank_basket(http, cfg, candidates())

    assert seen[3:] == ["/slots/price", "/basket/rank"]


async def test_the_fallback_log_names_the_http_status() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"code": "ml.unauthorized"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        with structlog.testing.capture_logs() as logs:
            await api.rank_basket(http, settings(), candidates())

    (line,) = [e for e in logs if e["event"] == "ml_fallback"]
    assert line["reason"] == "HTTPStatusError" and line["status"] == 401


# --- the job budget -------------------------------------------------------------------------------


async def test_once_the_run_has_spent_its_budget_its_calls_are_a_customers() -> None:
    clock = Clock()
    run = JobBudget(20.0, clock)
    waits: list[float] = []
    limits: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        waits.append(request.extensions["timeout"]["read"])
        limits.append(json.loads(request.content)["time_limit_s"])
        clock.now += 12  # each household costs 12 s of the planner
        return httpx2.Response(200, json={"not": "a plan"})

    cfg = settings(ml_breaker_threshold=0)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        for _ in range(4):
            await api.plan_week(http, cfg, plan_request(), job=run)

    # 12 s spent, 24 s spent (over 20): the third and fourth calls use ML_TIMEOUT_S and the
    # customer's 1 s solver limit.
    assert waits == [30.0, 30.0, 3.0, 3.0]
    assert limits == [5.0, 5.0, 1.0, 1.0]
    assert run.exhausted


async def test_a_spent_budget_is_a_customers_for_the_breaker_too() -> None:
    clock = Clock()
    run = JobBudget(1.0, clock)
    service = Service()
    for _ in range(3):
        await rank(service)  # open
    run.charge(clock.now - 5)  # spent
    assert run.exhausted
    service.up = True

    assert (await rank(service, job=run)).source == "fallback"  # no job probe privilege
    assert len(service.seen) == 3


@pytest.mark.parametrize(
    ("answer", "down"),
    [
        ("timeout", True),
        ("no_connection", True),
        (httpx2.Response(503), False),  # one path failing: its breaker counts it, the run asks on
        (httpx2.Response(422, json={}), False),  # a 4xx: the service is up
        (httpx2.Response(401, json={}), False),
    ],
)
async def test_a_failed_job_call_marks_the_service_down_for_the_rest_of_the_run(
    answer: object, down: bool
) -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        if answer == "timeout":
            raise httpx2.ReadTimeout("asleep", request=request)
        if answer == "no_connection":
            raise httpx2.ConnectError("refused", request=request)
        assert isinstance(answer, httpx2.Response)
        return answer

    pantry = PantryRequest(items=[PantryItem(product="milk", step=500, prior_mean=1400)])
    cfg = settings()
    run = JobBudget(20.0)
    with structlog.testing.capture_logs() as logs:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
            await api.plan_week(http, cfg, plan_request(), job=run)
            ranked = await api.rank_basket(http, cfg, candidates(), job=run)
            quantities = await api.pantry_quantities(http, cfg, pantry, job=run)
            another_plan = await api.plan_week(http, cfg, plan_request(), job=run)

    assert run.service_down is down
    assert ranked.source == quantities.source == another_plan.source == "fallback"
    if down:
        assert seen == ["/plan/week"]  # one request for every path of the run
        reasons = [e["reason"] for e in logs if e["event"] == "ml_fallback"]
        assert reasons[1:] == ["service_down"] * 3
    else:
        assert seen == ["/plan/week", "/basket/rank", "/pantry/quantities", "/plan/week"]
    # The mark belongs to the run: a customer's call (no budget) is not held back by it.
    assert ml_breakers.get("/basket/rank").state(COOLDOWN_S) == "closed"


async def test_the_service_down_mark_outlives_the_budget_and_is_not_shared_between_runs() -> None:
    seen: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.url.path)
        raise httpx2.ReadTimeout("asleep", request=request)

    cfg = settings()
    spent = JobBudget(1.0)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        await api.rank_basket(http, cfg, candidates(), job=spent)
        spent.charge(spent.now() - 5)  # the budget is gone as well
        assert spent.exhausted and spent.service_down
        await api.rank_basket(http, cfg, candidates(), job=spent)
        assert seen == [
            "/basket/rank"
        ]  # an exhausted run that saw the service down still asks nothing
        await api.rank_basket(http, cfg, candidates(), job=JobBudget(20.0))  # the next run asks
    assert len(seen) == 2


def test_the_job_budget_is_a_bounded_setting() -> None:
    assert make_settings().ml_job_budget_s == 20.0
    for bad in (0, 301):
        with pytest.raises(ValueError):
            make_settings(ml_job_budget_s=bad)


# --- the job-time timeout -------------------------------------------------------------------------


async def test_a_job_waits_ml_job_timeout_s_and_a_customer_ml_timeout_s() -> None:
    service = Service()
    cfg = settings(ml_timeout_s=3.0, ml_job_timeout_s=30.0)

    await rank(service, cfg)
    await rank(service, cfg, job=JobBudget(20.0))

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
        await api.plan_week(http, cfg, plan_request(), job=JobBudget(20.0))
        await api.pantry_quantities(http, cfg, pantry)
        await api.pantry_quantities(http, cfg, pantry, job=JobBudget(20.0))

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
        await api.plan_week(http, cfg, req, job=JobBudget(20.0) if job else None)

    assert seen[0]["time_limit_s"] == sent
