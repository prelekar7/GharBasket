"""HTTP endpoints for a household's Kindness donations (M9 §3.4): the list with the next pickup
dates, scheduling and cancelling. Customers never get photo links back."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from gharbasket.modules.community import donations
from gharbasket.modules.community.kindness_schemas import (
    DonationIn,
    DonationLine,
    DonationOut,
    DonationsOut,
    DriveRef,
    TimelineEntry,
)
from gharbasket.modules.community.models import Donation, DonationDrive
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Limit
from gharbasket.platform.permissions import Permit, require

router = APIRouter()


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


async def views(session: DbSession, found: list[Donation], lang: Language) -> list[DonationOut]:
    drive_ids = {d.drive_id for d in found if d.drive_id is not None}
    names = {
        drive.id: localised(drive.name, lang)
        for drive in (
            await session.execute(select(DonationDrive).where(DonationDrive.id.in_(drive_ids)))
        ).scalars()
    }
    timelines = await donations.timelines(session, [d.id for d in found])
    return [
        DonationOut(
            id=d.id,
            status=d.status,  # pyright: ignore[reportArgumentType]
            items=[DonationLine.model_validate(line) for line in d.items],
            photos=len(d.photo_keys),
            drive=None
            if d.drive_id is None
            else DriveRef(id=d.drive_id, name=names[d.drive_id], multiplier=float(d.multiplier)),
            pickup_on=d.pickup_on,
            estimated_points=d.estimated_points,
            points_awarded=d.points_awarded,
            reject_reason=d.reject_reason,  # pyright: ignore[reportArgumentType]
            cancel_reason=d.cancel_reason,  # pyright: ignore[reportArgumentType]
            ngo=None,
            timeline=[
                TimelineEntry(status=e.status, at=e.at)  # pyright: ignore[reportArgumentType]
                for e in timelines[d.id]
            ],
            created_at=d.created_at,
        )
        for d in found
    ]


@router.get(
    "/donations",
    summary="My household's donations, newest first, and the next two pickup dates",
    responses={422: _problem("request.invalid_cursor")},
)
async def my_donations(
    permit: Annotated[Permit, require("donations:read")],
    session: DbSession,
    lang: Lang,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> DonationsOut:
    household = await identity_api.household_for_user(session, permit.principal.user_id)
    if household is None:
        return DonationsOut(items=[], next_cursor=None, pickup_dates=[])
    found, next_cursor = await donations.page(session, household.id, cursor, limit)
    dates = await donations.pickup_dates(session, household.society_id, donations.now())
    return DonationsOut(
        items=await views(session, found, lang), next_cursor=next_cursor, pickup_dates=dates
    )


@router.post(
    "/donations",
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a donation pickup on one of the next pickup dates (Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        409: _problem("community.too_many_open_donations"),
        422: _problem(
            "community.no_items, community.item_code_unknown, community.donation_qty_invalid, "
            "community.pickup_date_invalid (dates), community.no_pickup_day, "
            "community.drive_not_open, community.photo_key_invalid, request.invalid"
        ),
        503: _problem("platform.storage_unavailable (photos sent while storage is down)"),
    },
)
async def schedule_donation(
    body: DonationIn,
    permit: Annotated[Permit, require("donations:create")],
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    request: Request,
) -> DonationOut:
    store = getattr(request.app.state, "storage", None)
    donation = await donations.schedule(session, permit.principal.user_id, body, store)
    out = (await views(session, [donation], lang))[0]
    await idem.save(201, out)
    await session.commit()
    return out


@router.post(
    "/donations/{donation_id}/cancel",
    summary="Cancel a donation still waiting for pickup (Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        404: _problem("community.donation_not_found"),
        409: _problem("community.donation_not_cancellable (donation_status)"),
    },
)
async def cancel_donation(
    donation_id: uuid.UUID,
    permit: Annotated[Permit, require("donations:cancel")],
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> DonationOut:
    user_id = permit.principal.user_id
    donation = await donations.own(session, user_id, donation_id)
    await donations.cancel(session, donation, "customer", user_id)
    out = (await views(session, [donation], lang))[0]
    await idem.save(200, out)
    await session.commit()
    return out
