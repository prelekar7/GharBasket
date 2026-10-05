"""HTTP endpoints of M12 Console & Analytics: the AI health table (the KPI and unit-economics
views follow)."""

from typing import Annotated

from fastapi import APIRouter, Response

from gharbasket.modules.analytics import permissions as _permissions  # noqa: F401
from gharbasket.modules.analytics import service
from gharbasket.modules.analytics.schemas import AiHealthOut
from gharbasket.platform.db import DbSession
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["analytics"])

CanRead = Annotated[Permit, require("analytics:read")]


@router.get(
    "/analytics/ai-health",
    summary="Every model's newest metrics, live drift flags and the ML-or-fallback share",
)
async def ai_health(permit: CanRead, session: DbSession, response: Response) -> AiHealthOut:
    response.headers["Cache-Control"] = "no-store"
    return await service.ai_health(session)
