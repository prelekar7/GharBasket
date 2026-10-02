"""Liveness endpoint used by Render's health check.

It must never touch the database or Valkey: Neon's free plan has a monthly compute allowance, and a
health check that queries the database every few seconds would keep it awake and use it up.
Readiness with dependency checks arrives separately as /v1/ready.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from gharbasket import __version__
from gharbasket.platform.settings import Settings, get_settings

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
