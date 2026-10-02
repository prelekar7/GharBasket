"""Liveness (`/v1/health`) and readiness (`/v1/ready`) endpoints.

Render's health check uses /v1/health, which must never touch the database or Valkey: Neon's free
plan has a monthly compute allowance, and a check that queries the database every few seconds would
keep it awake and use it up. /v1/ready checks the dependencies and is called only on demand
(after a deploy, or by a person).
"""

import asyncio
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from gharbasket import __version__
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings, get_settings

log = structlog.get_logger("gharbasket.health")

# A Neon compute waking up from suspend needs a moment, so the database gets a little longer.
DB_TIMEOUT_S = 3.0
VALKEY_TIMEOUT_S = 1.0

CheckResult = Literal["ok", "failed", "not_configured", "skipped"]

router = APIRouter(tags=["platform"])


class Health(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str


@router.get("/health", summary="Liveness check (does not touch the database)")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> Health:
    return Health(
        status="ok",
        service="gharbasket-api",
        version=__version__,
        environment=settings.app_env,
    )


class Ready(BaseModel):
    status: Literal["ok"]
    checks: dict[str, CheckResult]


async def _check_database(engine: AsyncEngine | None) -> CheckResult:
    if engine is None:
        return "not_configured"
    try:
        async with asyncio.timeout(DB_TIMEOUT_S), engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        # Only the type: driver messages can contain host names.
        log.warning("ready_check_failed", check="database", error=type(exc).__name__)
        return "failed"
    return "ok"


async def _check_valkey(valkey: Redis | None) -> CheckResult:
    if valkey is None:
        return "skipped"
    try:
        async with asyncio.timeout(VALKEY_TIMEOUT_S):
            await valkey.ping()
    except Exception as exc:
        log.warning("ready_check_failed", check="valkey", error=type(exc).__name__)
        return "failed"
    return "ok"


@router.get(
    "/ready",
    summary="Readiness check (database and Valkey)",
    responses={503: {"description": "A dependency is not reachable (problem+json)"}},
)
async def ready(request: Request) -> Ready:
    database, valkey = await asyncio.gather(
        _check_database(request.app.state.engine),
        _check_valkey(request.app.state.valkey),
    )
    checks: dict[str, CheckResult] = {"database": database, "valkey": valkey}
    failing = [name for name, result in checks.items() if result in ("failed", "not_configured")]
    if failing:
        raise ProblemError(
            503,
            "platform.not_ready",
            f"Not ready: {', '.join(failing)}.",
            errors=[{"check": name, "result": checks[name]} for name in failing],
        )
    return Ready(status="ok", checks=checks)
