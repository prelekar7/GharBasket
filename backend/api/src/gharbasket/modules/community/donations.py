"""Kindness donations (M9 §3.4): scheduling a pickup, the customer's list, and cancelling.

A household schedules items for one of its society's next two delivery dates (M3's delivery days),
each open until 22:00 IST the day before, ahead of M7's 23:15 route solve. Whoever brings the box
collects them at the door; the hub counts them and the household earns Kindness points.
At most three donations of a household are open (scheduled or picked up) at a time.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import drives, events
from gharbasket.modules.community.kindness_schemas import DonationIn
from gharbasket.modules.community.models import Donation, DonationDrive, DonationEvent, Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import files
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

PICKUP_DATES = 2
PICKUP_CLOSES = time(22, 0)  # IST, the day before
MAX_OPEN = 3
OPEN = ("scheduled", "picked_up")
PHOTO_PURPOSE = "donation_photo"
PHOTO_RETENTION = timedelta(days=90)


def now() -> datetime:
    return datetime.now(UTC)


def not_found() -> ProblemError:
    return ProblemError(404, "community.donation_not_found", "No such donation.")


def qty_invalid(code: str) -> ProblemError:
    return ProblemError(
        422,
        "community.donation_qty_invalid",
        "Pieces are whole, kilograms go in 0.5 steps, and each item has a limit.",
        extra={"code": code},
    )


def qty_ok(qty: Decimal, unit: str, most: Decimal, *, zero: bool = False) -> bool:
    """Whole pieces or 0.5 kg steps, more than 0 (or 0 when `zero`) and at most `most`."""
    steps = qty * 2 if unit == "kg" else qty
    return (qty >= 0 if zero else qty > 0) and qty <= most and steps == steps.to_integral_value()


def split_points(exact: list[Decimal]) -> list[int]:
    """Each line's points rounded half up; the largest line takes the remainder, so the lines add
    up to the rounded total."""
    lines = [drives.half_up(value) for value in exact]
    if lines:
        largest = max(range(len(exact)), key=lambda i: exact[i])
        lines[largest] += drives.half_up(sum(exact, Decimal(0))) - sum(lines)
    return lines


async def pickup_dates(session: AsyncSession, society_id: uuid.UUID, at: datetime) -> list[date]:
    """The society's next two delivery dates still open at `at`."""
    local = at.astimezone(drives.IST)
    after = local.date() if local.time() < PICKUP_CLOSES else local.date() + timedelta(days=1)
    return await delivery_dates(session, society_id, after, PICKUP_DATES)


async def delivery_dates(
    session: AsyncSession, society_id: uuid.UUID, after: date, n: int
) -> list[date]:
    # Imported here, not at the top: subscriptions.api imports identity.api, which imports this
    # module's facade for address checks, so a top-level import closes the cycle
    # identity -> community -> subscriptions -> identity.
    from gharbasket.modules.subscriptions import api as subscriptions_api

    return await subscriptions_api.delivery_dates(session, society_id, after, n)


def _no_pickup_day() -> ProblemError:
    return ProblemError(
        422, "community.no_pickup_day", "Your society has no delivery day for a pickup yet."
    )


async def record_event(
    session: AsyncSession, donation: Donation, status: str, by: uuid.UUID | None
) -> None:
    donation.status = status
    session.add(DonationEvent(donation_id=donation.id, status=status, by_user_id=by))
    await session.flush()


async def publish_scheduled(session: AsyncSession, donation: Donation) -> None:
    kg = sum(Decimal(str(line["qty"])) for line in donation.items if line["unit"] == "kg")
    await publish(
        session,
        events.DONATION_SCHEDULED,
        {
            "donation_id": str(donation.id),
            "user_id": str(donation.user_id),
            "household_id": str(donation.household_id),
            "hub_id": str(donation.hub_id),
            "society_id": str(donation.society_id),
            "pickup_on": donation.pickup_on.isoformat(),
            "items": [{k: line[k] for k in ("code", "unit", "qty")} for line in donation.items],
            "est_kg_tenths": int(kg * 10),
        },
    )


async def _claim_photos(
    session: AsyncSession, keys: list[str], user_id: uuid.UUID, store: files.ObjectStore | None
) -> list[str]:
    """The caller's own uploaded donation photos, checked in storage and claimed for M9."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return []
    if store is None:
        raise files.storage_unavailable()
    invalid = ProblemError(
        422, "community.photo_key_invalid", "Attach photos you uploaded for this donation."
    )
    used = await session.execute(select(Donation.id).where(Donation.photo_keys.overlap(keys)))
    if used.first() is not None:
        raise invalid
    for key in keys:
        upload = await files.verify(session, key, user_id=user_id, purpose=PHOTO_PURPOSE)
        stored = await store.head(key) if upload is not None else None
        if (
            upload is None
            or stored is None
            or (stored.content_type, stored.size_bytes) != (upload.content_type, upload.size_bytes)
            or await files.claim(
                session, key, user_id=user_id, purpose=PHOTO_PURPOSE, by="community"
            )
            is None
        ):
            raise invalid
    return keys


async def schedule(
    session: AsyncSession, user_id: uuid.UUID, body: DonationIn, store: files.ObjectStore | None
) -> Donation:
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        raise _no_pickup_day()
    if not body.items:
        raise ProblemError(422, "community.no_items", "Add at least one item.")
    rates = await drives.check_codes(session, [line.code for line in body.items])
    for line in body.items:
        rate = rates[line.code]
        if not qty_ok(line.qty, rate.unit, rate.max_qty):
            raise qty_invalid(line.code)
    dates = await pickup_dates(session, household.society_id, now())
    if not dates:
        raise _no_pickup_day()
    if body.pickup_on not in dates:
        raise ProblemError(
            422,
            "community.pickup_date_invalid",
            "Choose one of the next pickup dates.",
            extra={"dates": [d.isoformat() for d in dates]},
        )
    society = await session.get(Society, household.society_id)
    hub_id = household.hub_id or (society.hub_id if society else None)
    if hub_id is None:
        raise _no_pickup_day()
    drive: DonationDrive | None = None
    if body.drive_id is not None:
        drive = await session.get(DonationDrive, body.drive_id)
        if (
            drive is None
            or not drives.covers(drive, hub_id, household.society_id)
            or drives.drive_state(drive, body.pickup_on) != "running"
        ):
            raise ProblemError(
                422, "community.drive_not_open", "This drive is not open for this pickup."
            )
    # One household at a time, so two requests cannot both take the third open place.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"community.donations:{household.id}"},
    )
    open_now = await session.scalar(
        select(func.count())
        .select_from(Donation)
        .where(Donation.household_id == household.id, Donation.status.in_(OPEN))
    )
    if (open_now or 0) >= MAX_OPEN:
        raise ProblemError(
            409,
            "community.too_many_open_donations",
            f"You already have {MAX_OPEN} donations waiting for pickup or counting.",
        )
    keys = await _claim_photos(session, body.photo_keys, user_id, store)
    multiplier = drive.multiplier if drive is not None else Decimal("1.0")
    items = [
        {
            "code": line.code,
            "unit": rates[line.code].unit,
            "qty": float(line.qty),
            "counted": None,
            "rate": None,
            "points": None,
        }
        for line in body.items
    ]
    exact = [
        line.qty
        * rates[line.code].points
        * (multiplier if drive and line.code in drive.codes else 1)
        for line in body.items
    ]
    donation = Donation(
        user_id=user_id,
        household_id=household.id,
        hub_id=hub_id,
        society_id=household.society_id,
        drive_id=drive.id if drive else None,
        multiplier=multiplier,
        items=items,
        photo_keys=keys,
        pickup_on=body.pickup_on,
        estimated_points=sum(split_points(exact)),
    )
    session.add(donation)
    await session.flush()
    await session.refresh(donation)
    await record_event(session, donation, "scheduled", user_id)
    await publish_scheduled(session, donation)
    return donation


async def cancel(
    session: AsyncSession, donation: Donation, reason: str, by: uuid.UUID | None
) -> None:
    """Cancel a scheduled donation (the customer, or a second `not_ready`); its photos go 90
    days later."""
    if donation.status != "scheduled":
        raise ProblemError(
            409,
            "community.donation_not_cancellable",
            "Only a donation waiting for pickup can be cancelled.",
            extra={"donation_status": donation.status},
        )
    donation.cancel_reason = reason
    donation.version += 1
    for key in donation.photo_keys:
        await files.set_delete_after(session, key, now() + PHOTO_RETENTION)
    await record_event(session, donation, "cancelled", by)
    await publish(
        session,
        events.DONATION_CANCELLED,
        {
            "donation_id": str(donation.id),
            "hub_id": str(donation.hub_id),
            "society_id": str(donation.society_id),
            "pickup_on": donation.pickup_on.isoformat(),
            "reason": reason,
        },
    )


# --- pickup outcomes (M7 §3.6 reports them; the sweep catches the rest) -------------------------


async def record_pickup(
    session: AsyncSession,
    donation_id: uuid.UUID,
    outcome: str,
    *,
    by_user_id: uuid.UUID | None,
    on: date,
) -> Donation | None:
    """`collected` makes a scheduled donation `picked_up`; `not_ready` rolls it once to the
    society's next delivery date, then cancels it. None (nothing done) when it is not scheduled
    any more, so a redelivered report changes nothing."""
    donation = await session.get(Donation, donation_id, with_for_update=True)
    if donation is None or donation.status != "scheduled":
        return None
    if outcome != "collected":
        await not_ready(session, donation, by_user_id, today=on)
        return donation
    donation.picked_up_on, donation.picked_up_by_user_id = on, by_user_id
    donation.version += 1
    kept_until = datetime.combine(on, time(0), drives.IST) + PHOTO_RETENTION
    for key in donation.photo_keys:
        await files.set_delete_after(session, key, kept_until)
    await record_event(session, donation, "picked_up", by_user_id)
    return donation


async def on_pickup_completed(session: AsyncSession, event: OutboxEvent) -> None:
    """`logistics.pickup_completed` (M7 §3.6): each donation's outcome at the stop, by its rider
    on the route's date. A donation no longer scheduled is skipped, so a replay changes nothing."""
    payload = event.payload
    rider = payload.get("rider_user_id")
    on = date.fromisoformat(payload["delivery_date"])
    for outcome in payload.get("donations", []):
        await record_pickup(
            session,
            uuid.UUID(outcome["donation_id"]),
            outcome["status"],
            by_user_id=None if rider is None else uuid.UUID(rider),
            on=on,
        )


async def not_ready(
    session: AsyncSession, donation: Donation, by: uuid.UUID | None, *, today: date
) -> str:
    """The first miss moves the pickup to the next delivery date and tells M7 again; the second
    (or a society without another date) cancels it. Returns "rolled" or "cancelled"."""
    after = max(donation.pickup_on, today)
    following = await delivery_dates(session, donation.society_id, after, 1)
    if donation.pickup_misses == 0 and following:
        donation.pickup_misses, donation.pickup_on = 1, following[0]
        donation.version += 1
        await record_event(session, donation, "scheduled", by)
        await publish_scheduled(session, donation)
        return "rolled"
    await cancel(session, donation, "not_ready", by)
    return "cancelled"


async def sweep_missed(session: AsyncSession, today: date) -> dict[str, int]:
    """Scheduled donations due today (or before) that no stop reported count as not ready."""
    rows = await session.execute(
        select(Donation)
        .where(Donation.status == "scheduled", Donation.pickup_on <= today)
        .order_by(Donation.id)
        .with_for_update(skip_locked=True)
    )
    counts = {"rolled": 0, "cancelled": 0}
    for donation in rows.scalars().all():
        counts[await not_ready(session, donation, None, today=today)] += 1
    return counts


async def pickups_for(session: AsyncSession, society_id: uuid.UUID, on: date) -> list[Donation]:
    rows = await session.execute(
        select(Donation)
        .where(
            Donation.society_id == society_id,
            Donation.pickup_on == on,
            Donation.status == "scheduled",
        )
        .order_by(Donation.id)
    )
    return list(rows.scalars())


async def own(session: AsyncSession, user_id: uuid.UUID, donation_id: uuid.UUID) -> Donation:
    """A donation of the caller's household, locked; 404 otherwise."""
    household = await identity_api.household_for_user(session, user_id)
    donation = await session.get(Donation, donation_id, with_for_update=True)
    if donation is None or household is None or donation.household_id != household.id:
        raise not_found()
    return donation


async def page(
    session: AsyncSession, household_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[Donation], str | None]:
    """The household's donations, newest first."""
    stmt = select(Donation).where(Donation.household_id == household_id)
    if cursor is not None:
        try:
            stmt = stmt.where(Donation.id < uuid.UUID(decode_cursor(cursor)["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
    found = list(
        (await session.execute(stmt.order_by(Donation.id.desc()).limit(limit + 1))).scalars()
    )
    if len(found) <= limit:
        return found, None
    return found[:limit], encode_cursor({"id": str(found[limit - 1].id)})


async def timelines(
    session: AsyncSession, donation_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[DonationEvent]]:
    found: dict[uuid.UUID, list[DonationEvent]] = {i: [] for i in donation_ids}
    rows = await session.execute(
        select(DonationEvent)
        .where(DonationEvent.donation_id.in_(donation_ids))
        .order_by(DonationEvent.at, DonationEvent.id)
    )
    for row in rows.scalars():
        found[row.donation_id].append(row)
    return found


EXPORTED = (
    "id",
    "status",
    "items",
    "drive_id",
    "pickup_on",
    "picked_up_on",
    "estimated_points",
    "points_awarded",
    "reject_reason",
    "cancel_reason",
    "created_at",
)


async def export(session: AsyncSession, user_id: uuid.UUID) -> list[dict[str, Any]]:
    """The user's donations for M1's data export: items, counts, points, statuses and dates;
    photos counted, not included; never the hub's note."""
    rows = await session.execute(
        select(Donation).where(Donation.user_id == user_id).order_by(Donation.id)
    )
    return [
        {**{name: getattr(d, name) for name in EXPORTED}, "photos": len(d.photo_keys)}
        for d in rows.scalars()
    ]
