"""M7's part of a user's data export and erasure (M1 §3.3 and §3.5, spec Privacy).

Export: the household's deliveries (status, proof kind and times; never the photo, the delivery
code or the hub's note) and, for a rider, their routes and GPS pings of the last 90 days, plus
the handovers they made. Erasure: the rider's pings go; the household's door photos go (the
upload is made due at once, `sweep_uploads` deletes the object) and the OTP records are cleared;
user ids are nulled on handovers, deliveries, field events, routes and vehicles (the order
history keeps the actor kind). Route stops stay: they hold no flat and point at the society.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from geoalchemy2 import Geometry
from sqlalchemy import cast, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics.models import (
    Delivery,
    FieldEvent,
    GpsPing,
    Handover,
    Pickup,
    Route,
    Vehicle,
)
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import files

IST = ZoneInfo("Asia/Kolkata")
KEPT = timedelta(days=90)  # routes and pings in the export (handbook 13 §13.5)
MAX_PINGS = 100_000  # newest first; a 90-day trail of a full-time rider is about 90,000


async def household_orders(session: AsyncSession, user_id: uuid.UUID) -> list[uuid.UUID]:
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        return []
    return await orders_api.order_ids_of_household(session, household.id)


async def export(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    orders = await household_orders(session, user_id)
    rows = await session.scalars(
        select(Delivery)
        .where(Delivery.order_id.in_(orders))
        .order_by(Delivery.delivery_date, Delivery.order_id, Delivery.attempt)
    )
    deliveries = [
        {
            "order_id": d.order_id,
            "attempt": d.attempt,
            "delivery_date": d.delivery_date,
            "status": d.status,
            "reason": d.reason,
            "hold_until": None if d.hold_until is None else d.hold_until.strftime("%H:%M"),
            "proof": d.proof,  # the kind only: no photo, no code
            "refund_paise": d.refund_paise,
            "at": d.at,
        }
        for d in rows
    ]
    since = datetime.now(UTC) - KEPT
    routes = await session.scalars(
        select(Route)
        .where(Route.rider_user_id == user_id, Route.delivery_date >= since.astimezone(IST).date())
        .order_by(Route.delivery_date, Route.depart_time)
    )
    geometry = cast(GpsPing.location, Geometry)
    pings = await session.execute(
        select(
            GpsPing.at,
            GpsPing.route_id,
            func.ST_Y(geometry),
            func.ST_X(geometry),
            GpsPing.accuracy_m,
            GpsPing.speed_mps,
            GpsPing.heading,
        )
        .where(GpsPing.rider_user_id == user_id, GpsPing.at >= since)
        .order_by(GpsPing.at.desc())
        .limit(MAX_PINGS + 1)
    )
    trail = [
        {
            "at": at,
            "route_id": route_id,
            "lat": float(lat),
            "lng": float(lng),
            "accuracy_m": accuracy,
            "speed_mps": speed,
            "heading": heading,
        }
        for at, route_id, lat, lng, accuracy, speed, heading in pings.all()
    ]
    handovers = await session.scalars(
        select(Handover).where(Handover.user_id == user_id).order_by(Handover.at)
    )
    return {
        "deliveries": deliveries,
        "routes": [
            r.as_dict(exclude={"plan_id", "hub_id", "vehicle_id", "rider_user_id", "left_behind"})
            for r in routes
        ],
        "gps_pings": trail[:MAX_PINGS][::-1],  # oldest first
        "gps_pings_truncated": len(trail) > MAX_PINGS,
        "handovers": [
            {
                "at": h.at,
                "by_actor": h.by_actor,
                "crates_counted": h.crates_counted,
                "bags_counted": h.bags_counted,
                "mismatch": h.mismatch,
                "proof": h.proof,
            }
            for h in handovers
        ],
    }


async def anonymise(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 6). Returns the rows changed per table for the tombstone."""
    now = datetime.now(UTC)
    orders = (
        []
        if household_id is None
        else await orders_api.order_ids_of_household(session, household_id)
    )
    # The household's door proofs: the photo is made due now, the OTP record is cleared.
    proofs = list(
        await session.scalars(
            select(Delivery).where(
                Delivery.order_id.in_(orders),
                (Delivery.photo_key.is_not(None))
                | (Delivery.delivery_code != "")
                | (Delivery.note.is_not(None)),
            )
        )
    )
    for proof in proofs:
        if proof.photo_key is not None:
            await files.set_delete_after(session, proof.photo_key, now)
        proof.photo_key, proof.delivery_code, proof.code_attempts = None, "", 0
        proof.note = None  # the hub's or captain's note may quote the customer
    pings = await session.execute(delete(GpsPing).where(GpsPing.rider_user_id == user_id))
    # What the user did as a rider, captain or hub manager keeps its time and kind, not the person.
    handovers = await session.execute(
        update(Handover)
        .where(Handover.user_id == user_id)
        .values(user_id=None, partner_id=None, note=None)
    )
    attempts = await session.execute(
        update(Delivery).where(Delivery.user_id == user_id).values(user_id=None)
    )
    events = await session.execute(
        update(FieldEvent).where(FieldEvent.user_id == user_id).values(user_id=None)
    )
    routes = await session.execute(
        update(Route).where(Route.rider_user_id == user_id).values(rider_user_id=None)
    )
    vehicles = await session.execute(
        update(Vehicle)
        .where(Vehicle.default_rider_user_id == user_id)
        .values(default_rider_user_id=None)
    )
    # Pickups (§3.6) keep their counts: no reporter, and no household for the flat label.
    reported = await session.execute(
        update(Pickup).where(Pickup.by_user_id == user_id).values(by_user_id=None)
    )
    labelled = 0
    if household_id is not None:
        labelled = (
            await session.execute(
                update(Pickup).where(Pickup.household_id == household_id).values(household_id=None)
            )
        ).rowcount  # pyright: ignore[reportAttributeAccessIssue]
    return {
        "delivery_proofs": len(proofs),
        "gps_pings": pings.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "handovers": handovers.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "deliveries": attempts.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "field_events": events.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "routes": routes.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "vehicles": vehicles.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "pickups": reported.rowcount + labelled,  # pyright: ignore[reportAttributeAccessIssue]
    }
