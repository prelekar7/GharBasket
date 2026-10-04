"""Facade of the M10 Intelligence gateway: the only way other modules reach the ML service.

ML never blocks a customer (TDD §2): every call has a timeout, and when the ML service is unset,
slow, failing or refuses the token, a rule-based answer is returned instead, marked
`source: "fallback"` so callers and the console can tell.
"""

import uuid
from datetime import date

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import fallback, service
from gharbasket.modules.intel_gateway.schemas import PlanRequest, WeekPlan  # re-exported
from gharbasket.modules.intel_gateway.service import (
    latest_metrics,
    latest_prediction,
    price_spikes,
    record_metrics,
)
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.intel_gateway")

__all__ = [
    "PlanRequest",
    "WeekPlan",
    "latest_metrics",
    "latest_prediction",
    "plan_week",
    "price_spikes",
    "record_metrics",
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
    plan = await _plan_week(http, settings, request)
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


async def _plan_week(
    http: httpx2.AsyncClient, settings: Settings, request: PlanRequest
) -> WeekPlan:
    if not settings.ml_service_url:
        return fallback.plan_week(request)
    headers = {}
    if settings.ml_service_token is not None:
        headers["Authorization"] = f"Bearer {settings.ml_service_token.get_secret_value()}"
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        headers["X-Request-ID"] = str(request_id)
    try:
        response = await http.post(
            f"{settings.ml_service_url.rstrip('/')}/plan/week",
            json=request.model_dump(mode="json"),
            headers=headers,
            timeout=settings.ml_timeout_s,
        )
        response.raise_for_status()
        answer = response.json()
        return WeekPlan.model_validate({**answer, "source": "ml"})
    except (httpx2.HTTPError, ValueError) as exc:
        log.warning("ml_fallback", call="plan_week", reason=type(exc).__name__)
        return fallback.plan_week(request)
