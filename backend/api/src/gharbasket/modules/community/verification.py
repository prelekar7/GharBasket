"""The hub's side of donations (M9 §3.4): the verification queue, verifying with counted
quantities into points, and rejecting.

Points are computed at verification from the rates in force then: per line, counted x rate x
(the donation's drive multiplier when the drive covers the code, else 1), each line rounded half
up and the largest line taking the remainder. A donation is verified or rejected once, only after
pickup; `community.donation_verified` then carries the points, and M5's consumer credits them
(`credit_points(…, "donation", donation_id)`, M5 §3.6). Rejected items go to a recycler.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import donations, drives, events
from gharbasket.modules.community.kindness_schemas import RejectIn, VerifyIn
from gharbasket.modules.community.models import Donation, DonationDrive
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor
from gharbasket.platform.permissions import Permit

DONE = ("verified", "rejected", "handed_over")


async def staff_donation(session: AsyncSession, permit: Permit, donation_id: uuid.UUID) -> Donation:
    """A donation of the caller's hubs, locked; 404 otherwise."""
    donation = await session.get(Donation, donation_id, with_for_update=True)
    if donation is None or not permit.allows(donation.hub_id):
        raise donations.not_found()
    return donation


async def queue(
    session: AsyncSession,
    hub_ids: frozenset[uuid.UUID] | None,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Donation], str | None]:
    """Oldest first; `hub_ids` None means every hub (admin)."""
    stmt = select(Donation)
    if hub_ids is not None:
        stmt = stmt.where(Donation.hub_id.in_(hub_ids))
    if status is not None:
        stmt = stmt.where(Donation.status == status)
    if cursor is not None:
        try:
            stmt = stmt.where(Donation.id > uuid.UUID(decode_cursor(cursor)["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
    found = list((await session.execute(stmt.order_by(Donation.id).limit(limit + 1))).scalars())
    if len(found) <= limit:
        return found, None
    return found[:limit], encode_cursor({"id": str(found[limit - 1].id)})


def _picked_up(donation: Donation) -> None:
    if donation.status in DONE:
        raise ProblemError(
            409,
            "community.donation_already_verified",
            "This donation has already been verified or rejected.",
            extra={"donation_status": donation.status},
        )
    if donation.status != "picked_up":
        raise ProblemError(
            409,
            "community.donation_not_picked_up",
            "This donation has not been picked up.",
            extra={"donation_status": donation.status},
        )


async def verify(session: AsyncSession, donation: Donation, body: VerifyIn, by: uuid.UUID) -> None:
    _picked_up(donation)
    rates = await drives.check_codes(session, list(body.counted))
    for code, qty in body.counted.items():
        rate = rates[code]
        if not donations.qty_ok(qty, rate.unit, rate.max_qty * 2, zero=True):
            raise donations.qty_invalid(code)
    if not any(qty > 0 for qty in body.counted.values()):
        raise ProblemError(
            422, "community.nothing_counted", "Nothing was counted: reject the donation instead."
        )
    drive = (
        None if donation.drive_id is None else await session.get(DonationDrive, donation.drive_id)
    )
    declared = {line["code"] for line in donation.items}
    lines = [dict(line) for line in donation.items] + [
        {"code": code, "unit": rates[code].unit, "qty": 0}
        for code in body.counted
        if code not in declared
    ]
    exact: list[Decimal] = []
    for line in lines:
        rate = rates.get(line["code"])
        counted = body.counted.get(line["code"], Decimal(0))
        multiplier = donation.multiplier if drive and line["code"] in drive.codes else 1
        exact.append(counted * (rate.points if rate else 0) * multiplier)
        line.update(counted=float(counted), rate=rate.points if rate else None)
    for line, points in zip(lines, donations.split_points(exact), strict=True):
        line["points"] = points
    donation.items = lines
    donation.points_awarded = sum(line["points"] for line in lines)
    donation.has_unsuitable, donation.note = body.has_unsuitable, body.note
    donation.verified_by_user_id, donation.verified_at = by, donations.now()
    donation.version += 1
    await donations.record_event(session, donation, "verified", by)
    await publish(
        session,
        events.DONATION_VERIFIED,
        {
            "donation_id": str(donation.id),
            "user_id": str(donation.user_id),
            "household_id": str(donation.household_id),
            "hub_id": str(donation.hub_id),
            "society_id": str(donation.society_id),
            "drive_id": None if donation.drive_id is None else str(donation.drive_id),
            "points": donation.points_awarded,
        },
    )


async def reject(session: AsyncSession, donation: Donation, body: RejectIn, by: uuid.UUID) -> None:
    _picked_up(donation)
    donation.reject_reason, donation.note = body.reason, body.note
    donation.verified_by_user_id, donation.verified_at = by, donations.now()
    donation.version += 1
    await donations.record_event(session, donation, "rejected", by)
    await publish(
        session,
        events.DONATION_REJECTED,
        {
            "donation_id": str(donation.id),
            "user_id": str(donation.user_id),
            "hub_id": str(donation.hub_id),
            "reason": body.reason,
        },
    )
