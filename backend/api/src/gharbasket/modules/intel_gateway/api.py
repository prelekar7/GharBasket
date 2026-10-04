"""Facade of the M10 Intelligence gateway: the only way other modules reach the ML service.

ML never blocks a customer (TDD §2): every call has a timeout, and when the ML service is unset,
slow, failing or refuses the token, a rule-based answer is returned instead, marked
`source: "fallback"` so callers and the console can tell.
"""

import httpx2
import structlog

from gharbasket.modules.intel_gateway import fallback
from gharbasket.modules.intel_gateway.schemas import PlanRequest, WeekPlan
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.intel_gateway")


async def plan_week(http: httpx2.AsyncClient, settings: Settings, request: PlanRequest) -> WeekPlan:
    """A week of meals for one household (M10 §3.1): CP-SAT if reachable, else the rules."""
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
