"""HTTP endpoints of M8 for tiffin assignments (spec §3.4): hub staff run a round now."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from gharbasket.modules.partners import assignments, kitchens
from gharbasket.modules.partners.schemas import RunIn, RunOut
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["partners"])

CanManage = Annotated[Permit, require("assignments:manage")]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


@router.post(
    "/hubs/{hub_id}/tiffin-assignments/run",
    summary="Run one assignment round now for a date (hub manager, admin; Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def run_round(
    hub_id: uuid.UUID,
    body: RunIn,
    permit: CanManage,
    session: DbSession,
    idem: Idempotent,
    request: Request,
    settings: AppSettings,
) -> RunOut:
    permit.ensure(hub_id)
    result = await assignments.run_round(
        session,
        getattr(request.app.state, "http", None),
        settings,
        hub_id,
        body.date,
        at=kitchens.now(),
    )
    out = RunOut(
        run_id=result.run_ids[0] if result.run_ids else None,
        run_ids=result.run_ids,
        offers=result.offers,
        unassigned=result.unassigned,
    )
    await idem.save(200, out)
    await session.commit()
    return out
