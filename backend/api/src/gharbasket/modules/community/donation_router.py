"""HTTP endpoints for a household's Kindness donations (M9 §3.4): the list with the next pickup
dates, scheduling and cancelling. Customers never get photo links back."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status
from sqlalchemy import select

from gharbasket.modules.community import donations, drives, handoffs, verification
from gharbasket.modules.community.kindness_schemas import (
    DonationIn,
    DonationLine,
    DonationNgo,
    DonationOut,
    DonationsOut,
    DonationStatus,
    DriveRef,
    PhotoUrl,
    PickedUpBy,
    RejectIn,
    StaffDonationOut,
    StaffSociety,
    TimelineEntry,
    VerifyIn,
)
from gharbasket.modules.community.models import Donation, DonationDrive, Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit, files
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Limit, Page
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
    given_to = await handoffs.ngo_names_for(
        session, {d.handoff_id for d in found if d.handoff_id is not None}
    )
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
            ngo=None
            if d.handoff_id is None or d.handoff_id not in given_to
            else DonationNgo(name=given_to[d.handoff_id]),
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


# --- the hub: verification queue, verify and reject ---------------------------------------------

CanReview = Annotated[Permit, require("donations:review")]
CanVerify = Annotated[Permit, require("donations:verify")]


async def _staff_views(
    session: DbSession, permit: Permit, found: list[Donation], lang: Language, request: Request
) -> list[StaffDonationOut]:
    labels = await identity_api.delivery_labels(session, {d.household_id for d in found})
    societies = {
        s.id: s
        for s in (
            await session.execute(
                select(Society).where(Society.id.in_({d.society_id for d in found}))
            )
        ).scalars()
    }
    collectors = await identity_api.user_contacts(
        session, [d.picked_up_by_user_id for d in found if d.picked_up_by_user_id]
    )
    store: files.ObjectStore | None = getattr(request.app.state, "storage", None)
    out: list[StaffDonationOut] = []
    for view, d in zip(await views(session, found, lang), found, strict=True):
        society, label = societies[d.society_id], labels.get(d.household_id)
        towers = {str(t["id"]): str(t["label"]) for t in society.towers}
        photos: list[PhotoUrl] = []
        if store is not None and d.photo_keys:  # none while storage is down; `photos` still counts
            expires = donations.now() + files.GET_TTL
            photos = [
                PhotoUrl(url=store.signed_get(key), expires_at=expires) for key in d.photo_keys
            ]
            await audit.record(
                session,
                actor_user_id=permit.principal.user_id,
                action="community.donation_photo_links",
                entity_type="donation",
                entity_id=str(d.id),
                after={"photos": len(photos)},
            )
        collector = collectors.get(d.picked_up_by_user_id) if d.picked_up_by_user_id else None
        out.append(
            StaffDonationOut(
                **view.model_dump(),
                household_id=d.household_id,
                society=StaffSociety(id=society.id, name=society.name),
                tower=towers.get(label.tower_id, label.tower_id) if label else None,
                flat=label.flat if label else None,
                picked_up_by=None if collector is None else PickedUpBy(name=collector.name),
                picked_up_on=d.picked_up_on,
                photo_urls=photos,
            )
        )
    return out


@router.get(
    "/admin/donations",
    summary="The hub's donations, oldest first: the verification queue (5-minute photo links)",
    responses={
        403: _problem("auth.forbidden: another hub"),
        422: _problem("request.invalid_cursor"),
    },
)
async def hub_donations(
    permit: CanReview,
    session: DbSession,
    lang: Lang,
    request: Request,
    response: Response,
    hub_id: uuid.UUID | None = None,
    status: Annotated[DonationStatus | None, Query(description="e.g. picked_up")] = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[StaffDonationOut]:
    if hub_id is not None:
        permit.ensure(hub_id)
    hub_ids = (
        frozenset({hub_id})
        if hub_id is not None
        else None
        if drives.is_admin(permit)
        else frozenset(g.scope_id for g in permit.grants if g.scope_id is not None)
    )
    found, next_cursor = await verification.queue(session, hub_ids, status, cursor, limit)
    items = await _staff_views(session, permit, found, lang, request)
    await session.commit()  # the audit of minted photo links
    response.headers["Cache-Control"] = "no-store"
    return Page(items=items, next_cursor=next_cursor)


VERDICT = {
    400: _problem("request.idempotency_key_required"),
    404: _problem("community.donation_not_found (also outside your hubs)"),
    409: _problem(
        "community.donation_not_picked_up, community.donation_already_verified (donation_status)"
    ),
}


@router.post(
    "/donations/{donation_id}/verify",
    summary="Record what the hub counted; points are computed and sent to M5 (Idempotency-Key)",
    responses={
        **VERDICT,
        422: _problem(
            "community.item_code_unknown, community.donation_qty_invalid, "
            "community.nothing_counted, request.invalid"
        ),
    },
)
async def verify_donation(
    donation_id: uuid.UUID,
    body: VerifyIn,
    permit: CanVerify,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> DonationOut:
    donation = await verification.staff_donation(session, permit, donation_id)
    await verification.verify(session, donation, body, permit.principal.user_id)
    out = (await views(session, [donation], lang))[0]
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/donations/{donation_id}/reject",
    summary="Reject a picked-up donation; its items go to a recycler (Idempotency-Key)",
    responses={**VERDICT, 422: _problem("request.invalid")},
)
async def reject_donation(
    donation_id: uuid.UUID,
    body: RejectIn,
    permit: CanVerify,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> DonationOut:
    donation = await verification.staff_donation(session, permit, donation_id)
    await verification.reject(session, donation, body, permit.principal.user_id)
    out = (await views(session, [donation], lang))[0]
    await idem.save(200, out)
    await session.commit()
    return out
