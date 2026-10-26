"""Return-leg pickups (spec §3.6): what the van takes back at a society.

When a route starts, every drop stop gets its expected pickups as `logistics.pickups` rows: the
society's **open empties** (crates counted at its captain or hub handovers, minus the empties
collected since, minus those still expected on another stop; one row per stop), each **donation**
M9 scheduled for it that day (`community.api.pickups_for`) and each **bag** of a box refunded on its
second attempt while the captain held it (§3.4). A donation or bag already expected or collected on
another stop is not expected again. The rider reports parts of a stop (empties replace the count;
a donation or bag is set once); each report that changes something publishes
`logistics.pickup_completed`, which M9 (donations) and M6 (returned bags) consume. At route
completion what is still expected becomes `not_ready`.

The caller owns the transaction and has locked the route.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import case, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import events
from gharbasket.modules.logistics.models import (
    Delivery,
    Handover,
    Pickup,
    Route,
    RouteStop,
    Vehicle,
)
from gharbasket.modules.logistics.schemas import (
    BagPickupOut,
    DonationPickupOut,
    EmptiesTotal,
    HubPickupStop,
    PickupCount,
    PickupsIn,
    PickupTotals,
    SocietyRef,
    StopPickups,
    VehicleRef,
)
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

OPEN = ("expected", "collected")  # a donation or bag in one of these is on a stop already


def _now() -> datetime:
    return datetime.now(UTC)


def bag_order(code: str) -> uuid.UUID | None:
    """The order on a `GB1:` bag label (M6 §3.2), or None."""
    hex_id = code.strip().removeprefix("GB1:")
    if hex_id == code.strip() or len(hex_id) != 32:
        return None
    try:
        return uuid.UUID(hex_id)
    except ValueError:
        return None


async def open_empties(session: AsyncSession, society_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Crates left at each society (captain and hub handovers; a rider-mode handover leaves none)
    minus the empties collected there and those still expected on a stop, never below 0."""
    if not society_ids:
        return {}
    left = dict(
        (
            await session.execute(
                select(Handover.society_id, func.sum(Handover.crates_counted))
                .where(Handover.society_id.in_(society_ids), Handover.proof != "rider_mode")
                .group_by(Handover.society_id)
            )
        ).all()
    )
    taken = dict(
        (
            await session.execute(
                select(
                    Pickup.society_id,
                    func.sum(
                        func.coalesce(Pickup.collected_qty, 0)
                        + case((Pickup.status == "expected", Pickup.qty), else_=0)
                    ),
                )
                .where(Pickup.society_id.in_(society_ids), Pickup.kind == "empties")
                .group_by(Pickup.society_id)
            )
        ).all()
    )
    return {s: max(0, int(left.get(s) or 0) - int(taken.get(s) or 0)) for s in society_ids}


async def expect(session: AsyncSession, route: Route, stops: list[RouteStop]) -> None:
    """The expected pickups of each drop stop that has none yet (route start, or the first report
    on a route that started before §3.6 shipped).

    Each society is locked first, in id order, so two routes starting at once with a stop at the
    same society neither both expect its donations and bags nor count its open empties twice."""
    todo: list[tuple[RouteStop, uuid.UUID]] = []
    for stop in stops:
        if stop.kind != "drop" or stop.society_id is None:
            continue
        if await session.scalar(select(Pickup.id).where(Pickup.stop_id == stop.id).limit(1)):
            continue
        todo.append((stop, stop.society_id))
    for society_id in sorted({s for _, s in todo}):
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"logistics:pickups:{society_id}"},
        )
    for stop, society_id in todo:
        base = {
            "stop_id": stop.id,
            "route_id": route.id,
            "hub_id": route.hub_id,
            "delivery_date": route.delivery_date,
            "society_id": society_id,
        }
        empties = (await open_empties(session, [society_id]))[society_id]
        stop.empties_expected = empties
        session.add(Pickup(**base, kind="empties", qty=empties))
        taken = set(
            await session.scalars(
                select(Pickup.ref_id).where(
                    Pickup.society_id == society_id,
                    Pickup.kind.in_(("donation", "bag")),
                    Pickup.status.in_(OPEN),
                )
            )
        )
        for donation in await community_api.pickups_for(session, society_id, route.delivery_date):
            if donation.donation_id not in taken:
                session.add(
                    Pickup(
                        **base,
                        kind="donation",
                        ref_id=donation.donation_id,
                        household_id=donation.household_id,
                        items=donation.items_summary[:300],
                        qty=1,
                    )
                )
        held = await session.scalars(
            select(Delivery.order_id)
            .where(
                Delivery.society_id == society_id,
                Delivery.attempt == 2,
                Delivery.status == "refunded",
                Delivery.kept_by_captain.is_(True),
                Delivery.delivery_date < route.delivery_date,
            )
            .order_by(Delivery.order_id)
        )
        for order_id in list(held):
            if order_id not in taken:
                order = await orders_api.get(session, order_id)
                session.add(
                    Pickup(
                        **base, kind="bag", ref_id=order_id, household_id=order.household_id, qty=1
                    )
                )
        await session.flush()  # the next stop of the same society sees these rows


def _problem(status: int, code: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(status, code, detail, extra=extra or None)


async def report(
    session: AsyncSession, route: Route, stop: RouteStop, user_id: uuid.UUID, body: PickupsIn
) -> dict[str, Any] | None:
    """Set what the report names; publishes `logistics.pickup_completed` with what changed and
    returns its payload (None when nothing changed). The route is in progress (checked first)."""
    if stop.status == "skipped":
        raise _problem(409, "logistics.stop_skipped", "This stop was skipped; nothing was taken.")
    await expect(session, route, [stop])
    rows = list(
        await session.scalars(
            select(Pickup).where(Pickup.stop_id == stop.id).order_by(Pickup.id).with_for_update()
        )
    )
    empties_row = next((r for r in rows if r.kind == "empties"), None)
    if empties_row is None:
        raise _problem(422, "request.invalid", "This stop takes no pickups.")
    donations = {r.ref_id: r for r in rows if r.kind == "donation"}
    bags = {r.ref_id: r for r in rows if r.kind == "bag"}
    unknown_donations = [
        str(d.donation_id) for d in body.donations if d.donation_id not in donations
    ]
    scanned = {code: bag_order(code) for code in body.bags}
    unknown_bags = [code for code, order_id in scanned.items() if order_id not in bags]
    if unknown_donations or unknown_bags:
        raise _problem(
            422,
            "logistics.pickup_unknown",
            "Not expected at this stop.",
            donation_ids=unknown_donations,
            bags=unknown_bags,
        )
    order_ids = [o for o in scanned.values() if o is not None]
    clash_donations = [
        str(d.donation_id)
        for d in body.donations
        if donations[d.donation_id].status not in ("expected", d.status)
    ]
    clash_bags = [str(o) for o in order_ids if bags[o].status not in ("expected", "collected")]
    if clash_donations or clash_bags:
        raise _problem(
            409,
            "logistics.pickup_already_reported",
            "Already reported with the other status.",
            donation_ids=clash_donations,
            order_ids=clash_bags,
        )
    at = _now()

    def mark(row: Pickup, status: str, qty: int) -> None:
        row.status, row.collected_qty = status, qty
        row.by_user_id, row.client_at, row.reported_at = user_id, body.client_at, at

    changed_donations = []
    for d in body.donations:
        row = donations[d.donation_id]
        if row.status == "expected":
            mark(row, d.status, 1 if d.status == "collected" else 0)
            changed_donations.append({"donation_id": str(d.donation_id), "status": d.status})
    changed_bags = []
    for order_id in order_ids:
        if bags[order_id].status == "expected":
            mark(bags[order_id], "collected", 1)
            changed_bags.append(str(order_id))
    empties = None
    if body.empties is not None and (
        empties_row.collected_qty != body.empties or empties_row.status != "collected"
    ):
        mark(empties_row, "collected", body.empties)
        empties = body.empties
    if empties is None and not changed_donations and not changed_bags:
        return None
    payload = {
        "stop_id": str(stop.id),
        "route_id": str(route.id),
        "hub_id": str(route.hub_id),
        "society_id": str(stop.society_id),
        "delivery_date": route.delivery_date.isoformat(),
        "rider_user_id": str(user_id),
        "empties": empties,
        "donations": changed_donations,
        "bag_order_ids": changed_bags,
        "at": at.isoformat(),
    }
    await session.flush()
    await publish(session, events.PICKUP_COMPLETED, payload)
    return payload


async def close_open(session: AsyncSession, route: Route) -> None:
    """At route completion: what is still expected was not ready (no event; M9's sweep catches the
    donations, and a bag or empties are expected again at the society's next stop)."""
    await session.execute(
        update(Pickup)
        .where(Pickup.route_id == route.id, Pickup.status == "expected")
        .values(status="not_ready", reported_at=_now())
        .execution_options(synchronize_session=False)
    )


# --- reads ----------------------------------------------------------------------------------------


async def _flat_labels(session: AsyncSession, rows: list[Pickup]) -> dict[uuid.UUID, str]:
    """Tower label and flat of each household (M1's delivery labels, M9's tower names)."""
    households = sorted({r.household_id for r in rows if r.household_id is not None})
    if not households:
        return {}
    labels = await identity_api.delivery_labels(session, households)
    societies = await community_api.society_labels(session, sorted({r.society_id for r in rows}))
    towers: dict[str, str] = {}
    for society in societies.values():
        towers.update(society.towers)
    return {
        h: f"{towers.get(label.tower_id, label.tower_id)}, {label.flat}"
        for h, label in labels.items()
    }


async def stop_pickups(
    session: AsyncSession,
    route: Route,
    stops: list[RouteStop],
    *,
    with_flats: bool = False,
) -> dict[uuid.UUID, StopPickups]:
    """Each stop's pickups (stops without rows are left out). Flat labels on rider-mode stops of a
    route in progress, or everywhere `with_flats` (the hub's list)."""
    if not stops:
        return {}
    rows = list(
        await session.scalars(
            select(Pickup).where(Pickup.stop_id.in_([s.id for s in stops])).order_by(Pickup.id)
        )
    )
    by_stop: dict[uuid.UUID, list[Pickup]] = defaultdict(list)
    for row in rows:
        by_stop[row.stop_id].append(row)
    shown = {s.id for s in stops if with_flats or (s.rider_mode and route.status == "in_progress")}
    flats = await _flat_labels(session, [r for r in rows if r.stop_id in shown])
    out = {}
    for stop_id, found in by_stop.items():

        def flat(row: Pickup, stop_id: uuid.UUID = stop_id) -> str | None:
            if stop_id not in shown or row.household_id is None:
                return None
            return flats.get(row.household_id)

        empties = next((r for r in found if r.kind == "empties"), None)
        out[stop_id] = StopPickups(
            empties_collected=None if empties is None else empties.collected_qty,
            donations=[
                DonationPickupOut(
                    donation_id=r.ref_id,  # pyright: ignore[reportArgumentType]
                    items=r.items,
                    flat_label=flat(r),
                    status=r.status,  # pyright: ignore[reportArgumentType]
                )
                for r in found
                if r.kind == "donation"
            ],
            bags=[
                BagPickupOut(
                    order_id=r.ref_id,  # pyright: ignore[reportArgumentType]
                    flat_label=flat(r),
                    status=r.status,  # pyright: ignore[reportArgumentType]
                )
                for r in found
                if r.kind == "bag"
            ],
        )
    return out


async def hub_list(
    session: AsyncSession, hub_id: uuid.UUID, day: date
) -> tuple[PickupTotals, list[HubPickupStop]]:
    """The hub's stops with pickups on `day`, by departure, vehicle label, trip and seq."""
    with_rows = select(Pickup.stop_id).where(Pickup.hub_id == hub_id, Pickup.delivery_date == day)
    found = (
        await session.execute(
            select(RouteStop, Route, Vehicle)
            .join(Route, Route.id == RouteStop.route_id)
            .join(Vehicle, Vehicle.id == Route.vehicle_id)
            .where(RouteStop.id.in_(with_rows))
            .order_by(Route.depart_time, func.lower(Vehicle.label), Route.trip, RouteStop.seq)
        )
    ).all()
    names = await community_api.society_labels(
        session, sorted({s.society_id for s, _, _ in found if s.society_id is not None})
    )
    items = []
    for stop, route, vehicle in found:
        pickups = (await stop_pickups(session, route, [stop], with_flats=True))[stop.id]
        label = names.get(stop.society_id) if stop.society_id else None
        items.append(
            HubPickupStop(
                stop_id=stop.id,
                route_id=route.id,
                vehicle=VehicleRef(id=vehicle.id, label=vehicle.label, kind=vehicle.kind),  # pyright: ignore[reportArgumentType]
                seq=stop.seq,
                society=SocietyRef(id=stop.society_id, name=label.name if label else None),  # pyright: ignore[reportArgumentType]
                stop_status=stop.status,  # pyright: ignore[reportArgumentType]
                empties_expected=stop.empties_expected,
                pickups=pickups,
            )
        )
    counts = {
        (kind, status): int(n)
        for kind, status, n in (
            await session.execute(
                select(Pickup.kind, Pickup.status, func.count())
                .where(Pickup.hub_id == hub_id, Pickup.delivery_date == day)
                .group_by(Pickup.kind, Pickup.status)
            )
        ).all()
    }
    empties = (
        await session.execute(
            select(
                func.coalesce(func.sum(Pickup.qty), 0),
                func.coalesce(func.sum(Pickup.collected_qty), 0),
            ).where(Pickup.hub_id == hub_id, Pickup.delivery_date == day, Pickup.kind == "empties")
        )
    ).one()

    def count(kind: str) -> PickupCount:
        return PickupCount(
            expected=counts.get((kind, "expected"), 0),
            collected=counts.get((kind, "collected"), 0),
            not_ready=counts.get((kind, "not_ready"), 0),
        )

    totals = PickupTotals(
        empties=EmptiesTotal(expected=int(empties[0] or 0), collected=int(empties[1] or 0)),
        donations=count("donation"),
        bags=count("bag"),
    )
    return totals, items
