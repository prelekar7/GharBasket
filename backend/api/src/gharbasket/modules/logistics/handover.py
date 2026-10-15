"""The handover at a stop and the captain's day (spec §3.4).

The captain of a stop's society takes the crates by count with the rider's handover code (or a
photo of the crates); in rider mode the route's rider hands over to himself. Every dispatched order
of the stop then moves to `at_society` (actor `captain`, `rider` or `hub`) through `orders.api`.
When the counts do not match, only the bags she scanned move; the rest ride back with the van.
A captain acts only while M9 has her as the society's active captain (ADR-0026 §4). Flat labels
are tower and flat only, for her own society's orders of today and yesterday.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import attempts, events, tracking
from gharbasket.modules.logistics.execution import (
    ensure_running,
    route_orders,
    stop_not_found,
    tap,
)
from gharbasket.modules.logistics.models import Delivery, Handover, Route, RouteStop
from gharbasket.modules.logistics.routes import Caller, caller, conflict
from gharbasket.modules.logistics.routing import DETOUR, metres
from gharbasket.modules.logistics.schemas import CaptainFlat, FlatStatus, HandoverIn
from gharbasket.modules.logistics.views import now
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import files
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.permissions import Permit, forbidden

MAX_CODE_TRIES = 5
PHOTO_PURPOSE = "delivery_photo"  # M0
KINDS = ("box", "addon")
# order status -> what the captain's checklist calls it
FLAT_STATUS = {
    "locked": "on_the_way",
    "picking": "on_the_way",
    "packed": "on_the_way",
    "dispatched": "on_the_way",
    "at_society": "with_you",
    "delivered": "delivered",
    "undelivered": "undelivered",
    "redelivery": "undelivered",
    "refunded": "undelivered",
}


@dataclass(frozen=True)
class Actor:
    kind: str  # captain | rider | hub
    user_id: uuid.UUID
    partner_id: uuid.UUID | None = None


def not_society_captain() -> ProblemError:
    return ProblemError(
        403,
        "logistics.not_society_captain",
        "You are not the active captain of this society.",
    )


async def ensure_captain(
    session: AsyncSession, user_id: uuid.UUID, society_id: uuid.UUID
) -> uuid.UUID:
    """Her M9 partner id while she is the society's active captain, else 403."""
    captain = await community_api.captain_for_society(session, society_id)
    if captain is None or captain.user_id != user_id:
        raise not_society_captain()
    return captain.partner_id


async def stop_actor(
    session: AsyncSession, permit: Permit, stop_id: uuid.UUID
) -> tuple[RouteStop, Route, Actor]:
    """The stop (its route locked) and who acts on it: a hub manager of its hub, the route's rider
    on a rider-mode stop, or the captain of its society. 404 outside the caller's scope."""
    stop = await session.get(RouteStop, stop_id)
    if stop is None:
        raise stop_not_found()
    route = await session.get(Route, stop.route_id, with_for_update=True)
    assert route is not None
    await session.refresh(stop)
    who = caller(permit, route.hub_id)
    user_id = permit.principal.user_id
    if who.manager:
        return stop, route, Actor("hub", user_id)
    if who.rider and route.rider_user_id != user_id:
        raise ProblemError(403, "logistics.not_your_route", "This route is another rider's.")
    if who.rider:
        if not stop.rider_mode:
            raise forbidden("The captain takes this stop's bags; a hub manager can switch it.")
        return stop, route, Actor("rider", user_id)
    society = stop.society_id
    if society is not None and any(
        g.role == "captain" and g.scope_id == society for g in permit.grants
    ):
        if route.status == "draft":
            raise stop_not_found()
        return (
            stop,
            route,
            Actor("captain", user_id, await ensure_captain(session, user_id, society)),
        )
    if any(g.role == "captain" for g in permit.grants):
        raise forbidden("This stop is another society's.")
    raise stop_not_found()


async def photo_ok(
    session: AsyncSession, store: files.ObjectStore | None, key: str, user_id: uuid.UUID
) -> bool:
    """True for an uploaded photo of the caller with M0's `delivery_photo` purpose: its upload row
    names the caller and the purpose, and the object is in storage. Until M0 registers the purpose
    (PR #316) every photo key is refused, so an invented key never stands in for a code."""
    if PHOTO_PURPOSE not in files.PURPOSES or store is None:
        return False
    if await files.verify(session, key, user_id=user_id, purpose=PHOTO_PURPOSE) is None:
        return False
    return await store.head(key) is not None


def check_proof(
    route: Route, stop: RouteStop, actor: Actor, body: HandoverIn, photo_valid: bool
) -> str:
    """The handover's proof, or 422/429 (409 on a closed route or stop). A wrong code is counted
    on the stop (the caller commits the count before answering)."""
    ensure_running(route)
    if stop.status == "handed_over":
        raise conflict("logistics.already_handed_over", "This stop was handed over.")
    if actor.kind == "rider":
        return "rider_mode"
    if (body.handover_code is None) == (body.photo_key is None):
        raise ProblemError(
            422, "request.invalid", "Send exactly one of handover_code and photo_key."
        )
    if body.photo_key is not None:
        if not photo_valid:
            raise ProblemError(
                422, "logistics.photo_key_invalid", "Upload the photo as delivery_photo first."
            )
        return "photo"
    if stop.code_attempts >= MAX_CODE_TRIES:
        raise ProblemError(
            429,
            "logistics.code_attempts_exceeded",
            "Too many wrong codes for this stop; take a photo of the crates instead.",
        )
    if body.handover_code != stop.handover_code:
        stop.code_attempts += 1
        raise CodeInvalid()
    return "code"


class CodeInvalid(ProblemError):
    def __init__(self, detail: str = "That is not the rider's handover code.") -> None:
        super().__init__(422, "logistics.code_invalid", detail)


def bag_order_id(bag_qr: str) -> uuid.UUID:
    return uuid.UUID(bag_qr.removeprefix("GB1:"))


async def _distance_m(
    session: AsyncSession, valkey: Redis | None, route: Route, stop: RouteStop
) -> int | None:
    if stop.society_id is None:
        return None
    position = await tracking.latest(session, valkey, route.id)
    points = await community_api.society_points(session, [stop.society_id])
    gate = points.get(stop.society_id)
    if position is None or gate is None:
        return None
    return round(metres((position.lat, position.lng), gate) / DETOUR)


async def hand_over(
    session: AsyncSession,
    valkey: Redis | None,
    route: Route,
    stop: RouteStop,
    actor: Actor,
    body: HandoverIn,
    proof: str,
) -> tuple[Handover, list[uuid.UUID], list[uuid.UUID]]:
    """Move the stop's dispatched orders to `at_society`: all of them when the counts match, the
    scanned ones otherwise (409 `logistics.count_mismatch` without scans); `check_proof` first."""
    dispatched = [o.id for o in (await route_orders(session, route, ("dispatched",)))[stop.id]]
    # Redelivered bags: those back from the hub are counted; one the captain kept overnight is
    # not, yet it moves with the handover (§3.4).
    again = await attempts.riding(session, [stop.id])
    kept = {d.order_id for d in again if d.kept_by_captain}
    expected_bags = stop.bags + sum(not d.kept_by_captain for d in again)
    mismatch = (body.crates, body.bags) != (stop.crates, expected_bags)
    moving = dispatched
    if mismatch:
        if not body.scanned_bags:
            raise conflict(
                "logistics.count_mismatch",
                "The counts do not match what the van carries; scan the bags you have.",
                expected={"crates": stop.crates, "bags": expected_bags},
            )
        scanned = {bag_order_id(qr) for qr in body.scanned_bags}
        moving = [o for o in dispatched if o in scanned or o in kept]
    for order_id in moving:
        await orders_api.transition(
            session, order_id, "at_society", actor=actor.kind, actor_user_id=actor.user_id
        )
    await attempts.set_status(
        session, moving, "with_captain", by_actor=actor.kind, user_id=actor.user_id
    )
    at = now()
    handover = Handover(
        stop_id=stop.id,
        society_id=stop.society_id,
        by_actor=actor.kind,
        user_id=actor.user_id,
        partner_id=actor.partner_id,
        crates_expected=stop.crates,
        crates_counted=body.crates,
        bags_expected=expected_bags,
        bags_counted=body.bags,
        mismatch=mismatch,
        proof=proof,
        photo_key=body.photo_key,
        distance_m=await _distance_m(session, valkey, route, stop),
        note=body.note,
        client_at=body.client_at,
    )
    session.add(handover)
    stop.status, stop.handed_over_at = "handed_over", at
    stop.arrived_at = stop.arrived_at or at
    who = Caller(actor.user_id, actor.kind == "hub", False, actor.kind == "rider")
    tap(session, route, "handover", who, body.client_at, stop=stop, payload={"mismatch": mismatch})
    await session.flush()
    still = [o for o in dispatched if o not in moving]
    await publish(
        session,
        events.HANDOVER_COMPLETED,
        {
            "handover_id": str(handover.id),
            "stop_id": str(stop.id),
            "route_id": str(route.id),
            "hub_id": str(route.hub_id),
            "society_id": str(stop.society_id),
            "by_actor": actor.kind,
            "user_id": str(actor.user_id),
            "partner_id": None if actor.partner_id is None else str(actor.partner_id),
            "crates": body.crates,
            "bags": body.bags,
            "order_ids": [str(o) for o in moving],
            "mismatch": mismatch,
        },
    )
    return handover, moving, still


async def set_rider_mode(
    session: AsyncSession, route: Route, stop: RouteStop, who: Caller, on: bool, expected: int
) -> None:
    """A hub manager switches a stop to rider mode for the day (an absent captain), audited."""
    if route.status in ("completed", "cancelled"):
        raise conflict("logistics.route_closed", f"This route is {route.status}.")
    if route.version != expected:
        raise ProblemError(
            412, "request.version_mismatch", "This changed since you loaded it; reload and retry."
        )
    if stop.status == "handed_over":
        raise conflict("logistics.already_handed_over", "This stop was handed over.")
    if stop.rider_mode != on:
        stop.rider_mode = on
        tap(session, route, "rider_mode", who, None, stop=stop, payload={"rider_mode": on})
        await session.flush()


# --- the captain's and the rider's lists ------------------------------------------------------


async def flats(
    session: AsyncSession,
    hub_id: uuid.UUID,
    day: date,
    society_id: uuid.UUID,
    slot_id: uuid.UUID | None = None,
) -> list[CaptainFlat]:
    """The society's box and add-on orders of the day (one stop's when `slot_id` is given), and
    the redeliveries booked on the day (`redelivery: true`), by tower and flat, with tower and
    flat labels only."""
    found = await orders_api.orders_for_delivery(
        session, hub_id, day, tuple(FLAT_STATUS), kinds=KINDS
    )
    orders = [
        s.order
        for s in found
        if s.order.society_id == society_id and (slot_id is None or s.order.slot_id == slot_id)
    ]
    again = await attempts.redeliveries_on(session, society_id, day, slot_id)
    orders += [await orders_api.get(session, a.order_id) for a in again]
    redelivered = {a.order_id for a in again}
    labels = await identity_api.delivery_labels(session, [o.household_id for o in orders])
    tries = {o.id: await attempts.latest(session, o.id) for o in orders}
    society = (await community_api.society_labels(session, [society_id])).get(society_id)
    towers = society.towers if society else {}
    rows = []
    for order in orders:
        label = labels.get(order.household_id)
        tower = towers.get(label.tower_id, label.tower_id) if label else ""
        rows.append(
            (
                (tower, label.flat if label else ""),
                CaptainFlat(
                    order_id=order.id,
                    flat_label=f"{tower}, {label.flat}" if label else None,
                    kind=order.kind,  # pyright: ignore[reportArgumentType]
                    bags=1,
                    redelivery=order.id in redelivered,
                    status="on_the_way"
                    if order.status == "redelivery"
                    else _flat_status(order.status, tries[order.id]),
                    hold_until=_hold(tries[order.id]),
                ),
            )
        )
    return [flat for _, flat in sorted(rows, key=lambda r: (_natural(r[0][0]), _natural(r[0][1])))]


def _natural(value: str) -> tuple[object, ...]:
    return tuple(int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", value))


async def society_stops(
    session: AsyncSession, society_id: uuid.UUID, day: date
) -> list[tuple[RouteStop, Route]]:
    """The society's stops on the day's published, started or completed routes."""
    rows = await session.execute(
        select(RouteStop, Route)
        .join(Route, Route.id == RouteStop.route_id)
        .where(
            RouteStop.society_id == society_id,
            Route.delivery_date == day,
            Route.status.in_(("published", "in_progress", "completed")),
        )
        .order_by(RouteStop.eta)
    )
    return [(stop, route) for stop, route in rows.all()]


def _flat_status(order_status: str, attempt: Delivery | None) -> FlatStatus:
    if attempt is not None and attempt.status == "held" and order_status == "at_society":
        return "held"
    return FLAT_STATUS[order_status]  # pyright: ignore[reportReturnType]


def _hold(attempt: Delivery | None) -> str | None:
    if attempt is None or attempt.status != "held" or attempt.hold_until is None:
        return None
    return attempt.hold_until.strftime("%H:%M")
