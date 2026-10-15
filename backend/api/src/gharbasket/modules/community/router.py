"""HTTP endpoints of M9 Community & Kindness: society search, unlock and requests (spec §3.1),
and captain appointments (§3.2, captain_router.py); point rates and drives (§3.4, drive_router.py),
and donations (donation_router.py).
"""

import uuid
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import jobs as _jobs  # noqa: F401 (registers the consumers)
from gharbasket.modules.community import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.community import service
from gharbasket.modules.community.captain_router import router as captain_router
from gharbasket.modules.community.donation_router import router as donation_router
from gharbasket.modules.community.drive_router import router as drive_router
from gharbasket.modules.community.models import Society
from gharbasket.modules.community.schemas import (
    CaptainCard,
    SocietyOut,
    SocietyRequestIn,
    SocietyRequestOut,
    SocietySummary,
    Tower,
    UnlockOut,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.valkey import Valkey

router = APIRouter(tags=["community"])
router.include_router(captain_router)
router.include_router(drive_router)
router.include_router(donation_router)

SEARCH_LIMIT = 50
# Society requests per user per day (spec §3.1).
REQUESTS_PER_DAY = 5


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _summary(society: Society, distance_m: int | None) -> SocietySummary:
    return SocietySummary(
        id=society.id,
        slug=society.slug,
        name=society.name,
        area=society.area,
        status=society.status,  # pyright: ignore[reportArgumentType]
        distance_m=distance_m,
    )


@router.get(
    "/societies",
    summary="Find societies by name or area, or the nearest ones",
    responses={
        401: _problem("auth.invalid_token"),
        422: _problem("community.near_invalid, request.invalid_cursor, request.invalid"),
    },
)
async def search_societies(
    _principal: CurrentPrincipal,
    session: DbSession,
    q: Annotated[
        str | None,
        Query(max_length=80, description="Words of the name or area; typos and accents are fine"),
    ] = None,
    near: Annotated[
        str | None, Query(max_length=40, description="`lat,lng`", examples=["19.0390,73.0690"])
    ] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=SEARCH_LIMIT, description="Items per page")] = 20,
) -> Page[SocietySummary]:
    found, next_cursor = await service.search(
        session,
        q=" ".join(q.split()) if q else None,
        near=service.parse_near(near) if near else None,
        cursor=cursor,
        limit=limit,
    )
    return Page(
        items=[_summary(hit.society, hit.distance_m) for hit in found], next_cursor=next_cursor
    )


@router.get(
    "/societies/{society_id}",
    summary="A society with its towers (and its captain, for residents)",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("community.society_not_found"),
    },
)
async def get_society(
    society_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> SocietyOut:
    society = await service.get(session, society_id)
    # The captain's card goes only to residents of this society (§3.1, §3.2).
    household = await identity_api.household_for_user(session, principal.user_id)
    card = None
    if household is not None and household.society_id == society.id:
        card = await community_api.captain_card(session, society.id)
    return SocietyOut(
        **_summary(society, None).model_dump(),
        towers=[Tower.model_validate(tower) for tower in society.towers],
        flats=society.flats,
        captain=None if card is None else CaptainCard(**asdict(card)),
    )


@router.get(
    "/societies/{society_id}/unlock",
    summary="How many households have joined, and how many unlock free delivery",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("community.society_not_found"),
    },
)
async def society_unlock(
    society_id: uuid.UUID, _principal: CurrentPrincipal, session: DbSession
) -> UnlockOut:
    society = await service.get(session, society_id)
    return UnlockOut(
        society_id=society.id,
        status=society.status,  # pyright: ignore[reportArgumentType]
        joined=await service.joined(session, society.id),
        threshold=society.unlock_threshold,
    )


@router.post(
    "/society-requests",
    status_code=status.HTTP_201_CREATED,
    summary="Ask GharBasket to come to a society that isn't listed (Idempotency-Key required)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
        429: _problem("community.request_rate_limited (see Retry-After)"),
    },
)
async def request_society(
    body: SocietyRequestIn,
    permit: Annotated[Permit, require("society_requests:create")],
    session: DbSession,
    idem: Idempotent,
    valkey: Valkey,
) -> SocietyRequestOut:
    user_id = permit.principal.user_id
    await rate_limit.enforce(
        valkey,
        f"society-requests:user:{user_id}",
        limit=REQUESTS_PER_DAY,
        window_s=24 * 60 * 60,
        code="community.request_rate_limited",
        detail="You have sent enough society requests for today. Try again tomorrow.",
    )
    created = SocietyRequestOut.model_validate(await service.create_request(session, user_id, body))
    await idem.save(201, created)
    await session.commit()
    return created
