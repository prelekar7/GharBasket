"""Facade of the M10 Intelligence gateway: the only way other modules reach the ML service.

ML never blocks a customer (TDD §2): every call has a timeout, and when the ML service is unset,
slow, failing or refuses the token, a rule-based answer is returned instead, marked
`source: "fallback"` so callers and the console can tell.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import fallback, service
from gharbasket.modules.intel_gateway.schemas import (  # re-exported
    AssignRequest,
    KitchenAssignment,
    PantryItem,
    PantryObservation,
    PantryQuantities,
    PantryRequest,
    PlanRequest,
    RoutePlan,
    RouteRequest,
    WeekPlan,
)
from gharbasket.modules.intel_gateway.service import (
    latest_metrics,
    latest_prediction,
    price_spikes,
    record_metrics,
)
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.intel_gateway")

ASSIGN_MARGIN_S = 5.0  # over the solver's time limit: candidates, transfer, a cold start

__all__ = [
    "AssignRequest",
    "KitchenAssignment",
    "MetricRow",
    "ModelHealth",
    "PantryItem",
    "PantryObservation",
    "PantryQuantities",
    "PantryRequest",
    "PlanRequest",
    "PredictionCount",
    "RoutePlan",
    "RouteRequest",
    "WeekPlan",
    "assign_kitchens",
    "latest_metrics",
    "latest_prediction",
    "model_health",
    "pantry_quantities",
    "plan_week",
    "price_spikes",
    "record_metrics",
    "route_timeout_s",
    "solve_routes",
]


async def plan_week(
    http: httpx2.AsyncClient,
    settings: Settings,
    request: PlanRequest,
    *,
    session: AsyncSession | None = None,
    household_id: uuid.UUID | None = None,
    week_monday: date | None = None,
) -> WeekPlan:
    """A week of meals for one household (M10 §3.1): CP-SAT if reachable, else the rules.

    With `session`, `household_id` and `week_monday` the answer is also kept in
    `intel.predictions` (kind `weekly_plan`, source `ml` or `fallback`); the caller commits.
    """
    plan = await _call(
        http,
        settings,
        "/plan/week",
        request.model_dump(mode="json") | {"time_limit_s": _solver_budget(settings, request)},
        parse=lambda body: WeekPlan.model_validate({**body, "source": "ml"}),
        fallback_to=lambda: fallback.plan_week(request),
        call="plan_week",
    )
    if session is not None and household_id is not None and week_monday is not None:
        await service.record_prediction(
            session,
            kind="weekly_plan",
            subject_type="household",
            subject_id=household_id,
            for_date=week_monday,
            source=plan.source,
            payload=plan.model_dump(mode="json", exclude={"source", "model"}),
            model_name=plan.model,
        )
    return plan


async def pantry_quantities(
    http: httpx2.AsyncClient,
    settings: Settings,
    request: PantryRequest,
    *,
    session: AsyncSession | None = None,
    household_id: uuid.UUID | None = None,
    for_date: date | None = None,
) -> PantryQuantities:
    """Weekly use per item from the pantry twin (M10 §3.3): the ML service if reachable, else
    the same closed form in plain arithmetic. With `session`, `household_id` and `for_date` the
    answer is kept in `intel.predictions` (kind `pantry`); the caller commits."""
    answer = await _call(
        http,
        settings,
        "/pantry/quantities",
        request.model_dump(mode="json"),
        parse=lambda body: PantryQuantities.model_validate({**body, "source": "ml"}),
        fallback_to=lambda: fallback.pantry_quantities(request),
        call="pantry_quantities",
    )
    if session is not None and household_id is not None and for_date is not None:
        await service.record_prediction(
            session,
            kind="pantry",
            subject_type="household",
            subject_id=household_id,
            for_date=for_date,
            source=answer.source,
            payload=answer.model_dump(mode="json", exclude={"source", "model"}),
            model_name=answer.model,
        )
    return answer


@dataclass(frozen=True)
class MetricRow:
    metric: str
    value: float
    split: str
    data_source: str
    version: str
    computed_at: datetime


@dataclass(frozen=True)
class ModelHealth:
    model_name: str
    metrics: tuple[MetricRow, ...]
    drift: bool  # the newest live `drift` row says 1


@dataclass(frozen=True)
class PredictionCount:
    kind: str
    source: str  # ml | fallback
    count: int


async def model_health(
    session: AsyncSession, *, since: datetime
) -> tuple[list[ModelHealth], list[PredictionCount]]:
    """What the console's AI health table shows (M12): every model's newest value per
    (metric, split, data source) from `model_metrics`, its drift flag, and how often each kind
    of prediction came from the ML service or the fallback since `since`."""
    models = []
    for name in await service.model_names(session):
        rows = await service.latest_metrics(session, name)
        models.append(
            ModelHealth(
                name,
                tuple(
                    MetricRow(m.metric, m.value, m.split, m.data_source, m.version, m.computed_at)
                    for m in rows
                ),
                any(m.metric == "drift" and m.split == "live" and m.value >= 1 for m in rows),
            )
        )
    counts = [
        PredictionCount(kind, source, count)
        for kind, source, count in await service.prediction_counts(session, since)
    ]
    return models, counts


async def hub_forecast(
    session: AsyncSession, hub_id: uuid.UUID, for_date: date
) -> dict[str, Any] | None:
    """The latest demand forecast stored for a hub and delivery day (`for_date` is the delivery
    day, any weekday; the forecast job's lines: `items[{product_id, for_date, committed,
    uncommitted_q50, uncommitted_qtau, order_qty, model}]` and `data_source`); None when the job
    has not run for that day. M6 builds its procurement plan from it (M6 §6) on
    `intel.forecast_ready`."""
    kept = await service.latest_prediction(
        session, kind="forecast", subject_id=hub_id, for_date=for_date
    )
    return None if kept is None else {**kept.payload, "model": kept.model_name}


async def assign_kitchens(
    http: httpx2.AsyncClient,
    settings: Settings,
    request: AssignRequest,
    *,
    session: AsyncSession | None = None,
    hub_id: uuid.UUID | None = None,
    for_date: date | None = None,
) -> KitchenAssignment | None:
    """Tomorrow's tiffin portions to kitchens from the ML service (M10 §3.5), kept in
    `intel.predictions` (kind `assignment`, subject the hub). `status: partial` lists the orders
    no kitchen could take in `unassigned`. None when the service does not answer within
    `time_limit_s` plus a margin: M8's job assigns greedily by distance itself (M8 §6)."""
    if not settings.ml_service_url:
        return None
    headers = {}
    if settings.ml_service_token is not None:
        headers["Authorization"] = f"Bearer {settings.ml_service_token.get_secret_value()}"
    try:
        response = await http.post(
            f"{settings.ml_service_url.rstrip('/')}/assign/kitchens",
            json=request.model_dump(mode="json"),
            headers=headers,
            timeout=max(settings.ml_timeout_s, request.time_limit_s + ASSIGN_MARGIN_S),
        )
        response.raise_for_status()
        plan = KitchenAssignment.model_validate(response.json())
    except (httpx2.HTTPError, ValueError) as exc:
        log.warning("ml_fallback", call="assign_kitchens", reason=type(exc).__name__)
        return None
    if session is not None and hub_id is not None and for_date is not None:
        await service.record_prediction(
            session,
            kind="assignment",
            subject_type="hub",
            subject_id=hub_id,
            for_date=for_date,
            source="ml",
            payload=plan.model_dump(mode="json"),
            model_name=plan.model,
        )
    return plan


def route_timeout_s(settings: Settings, request: RouteRequest) -> float:
    """The solver's own limit plus the ML service's OSRM wait and a margin."""
    return max(settings.ml_timeout_s, request.time_limit_s + settings.ml_route_overhead_s)


async def solve_routes(
    http: httpx2.AsyncClient,
    settings: Settings,
    request: RouteRequest,
    *,
    session: AsyncSession | None = None,
    hub_id: uuid.UUID | None = None,
    for_date: date | None = None,
) -> RoutePlan | None:
    """One slot's van routes from the ML service (M10 §3.4), kept in `intel.predictions`
    (kind `route`, subject the hub). None when the service does not answer within
    `route_timeout_s`: there is no rule-based routing in the API, so M7 falls back to the
    previous day's sequence (M7 §6). M7 publishes a plan only when `summary.problems` is
    empty."""
    if not settings.ml_service_url:
        return None
    timeout = route_timeout_s(settings, request)
    headers = {}
    if settings.ml_service_token is not None:
        headers["Authorization"] = f"Bearer {settings.ml_service_token.get_secret_value()}"
    try:
        response = await http.post(
            f"{settings.ml_service_url.rstrip('/')}/route/solve",
            json=request.model_dump(mode="json"),
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        plan = RoutePlan.model_validate(response.json())
    except (httpx2.HTTPError, ValueError) as exc:
        log.warning("ml_fallback", call="solve_routes", reason=type(exc).__name__)
        return None
    if session is not None and hub_id is not None and for_date is not None:
        await service.record_prediction(
            session,
            kind="route",
            subject_type="hub",
            subject_id=hub_id,
            for_date=for_date,
            source="ml",
            payload=plan.model_dump(mode="json"),
            model_name=plan.model,
        )
    return plan


async def _call[T](
    http: httpx2.AsyncClient,
    settings: Settings,
    path: str,
    body: dict[str, Any],
    *,
    parse: Callable[[dict[str, Any]], T],
    fallback_to: Callable[[], T],
    call: str,
) -> T:
    """POST `body` to the ML service at `path` within `ml_timeout_s` and `parse` the answer; on
    no service, a timeout, an HTTP error or a malformed body, `fallback_to()` answers instead."""
    if not settings.ml_service_url:
        return fallback_to()
    headers = {}
    if settings.ml_service_token is not None:
        headers["Authorization"] = f"Bearer {settings.ml_service_token.get_secret_value()}"
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        headers["X-Request-ID"] = str(request_id)
    try:
        response = await http.post(
            f"{settings.ml_service_url.rstrip('/')}{path}",
            json=body,
            headers=headers,
            timeout=settings.ml_timeout_s,
        )
        response.raise_for_status()
        return parse(response.json())
    except (httpx2.HTTPError, ValueError, TypeError) as exc:  # pydantic errors are ValueErrors
        log.warning("ml_fallback", call=call, reason=type(exc).__name__)
        return fallback_to()


# The solver gets at most this share of the HTTP timeout; the rest covers the network, the
# model build and the JSON both ways, so a slow solve still answers before the gateway gives up.
SOLVER_SHARE_OF_TIMEOUT = 0.5


def _solver_budget(settings: Settings, request: PlanRequest) -> float:
    """The request's `time_limit_s`, capped so the ML call ends inside `ml_timeout_s`."""
    return max(0.1, min(request.time_limit_s, SOLVER_SHARE_OF_TIMEOUT * settings.ml_timeout_s))
