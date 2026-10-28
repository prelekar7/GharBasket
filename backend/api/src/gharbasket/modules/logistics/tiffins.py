"""A tiffin route on the day (spec §3.7): the rider's collect at a kitchen, the handover of a
captain's drop by count, and the route's end.

Collect scans the kitchen's `GB1A:` code through `partners.api.pickup` and moves each ready tiffin
of the stop `packed → dispatched` as the route's rider, opening its attempt on its drop. A captain
takes a drop's tiffins by count and code (no M4 move: tiffins skip `at_society`); the doors are
§3.4's proof and undelivered (`deliveries.py`). At the end, tiffins still with the rider are
refunded at once, tiffins never collected go to the hub's triage (`left_behind`), and at 16:00
whatever is still out goes to triage (`not_confirmed`): M4 lets no `system` move a tiffin there.
"""

import uuid
from datetime import date

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import attempts, deliveries, execution, handover
from gharbasket.modules.logistics.handover import Actor
from gharbasket.modules.logistics.models import Delivery, Route, RouteStop, StopOrder
from gharbasket.modules.logistics.routes import Caller, conflict
from gharbasket.modules.logistics.schemas import (
    CollectedTiffin,
    CollectIn,
    HandoverIn,
    WaitingTiffin,
)
from gharbasket.modules.logistics.views import now, stops_of
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.errors import ProblemError

OUT = ("on_route", "with_captain")  # with the rider or a captain, not settled yet
NOT_YET = ("locked", "picking", "packed")  # still at the kitchen


def _invalid(code: str, detail: str) -> ProblemError:
    return ProblemError(422, f"logistics.{code}", detail)


async def _rows(session: AsyncSession, route: Route) -> dict[uuid.UUID, list[StopOrder]]:
    """The route's stop orders by stop."""
    stops = [s.id for s in await stops_of(session, route.id)]
    found = await session.execute(
        select(StopOrder).where(StopOrder.stop_id.in_(stops)).order_by(StopOrder.short_code)
    )
    out: dict[uuid.UUID, list[StopOrder]] = {s: [] for s in stops}
    for row in found.scalars():
        out[row.stop_id].append(row)
    return out


async def collect(
    session: AsyncSession, route: Route, stop: RouteStop, who: Caller, body: CollectIn
) -> tuple[list[CollectedTiffin], list[uuid.UUID], list[WaitingTiffin], list[uuid.UUID]]:
    """The rider's scan at a kitchen stop: (collected, already, not_ready, not_on_route)."""
    if route.rider_user_id != who.user_id:  # not a hub manager for him either (§3.7, q. 8)
        raise ProblemError(403, "logistics.not_your_route", "This route is another rider's.")
    execution.ensure_running(route)
    if stop.kind != "kitchen":
        raise _invalid("not_a_kitchen_stop", "Collect at a kitchen stop.")
    if stop.status == "skipped":
        raise conflict("logistics.stop_skipped", "This stop was skipped.")
    try:
        ready = await partners_api.pickup(session, body.pickup_qr, route.hub_id)
    except ProblemError as exc:
        if exc.code != "partners.pickup_invalid":
            raise
        raise _invalid("pickup_code_invalid", "This is not a kitchen code of this hub.") from None
    if ready.assignment_id != stop.assignment_id:
        raise _invalid("pickup_wrong_kitchen", "This code is another kitchen's.")
    rows = await _rows(session, route)
    stops = {s.id: s for s in await stops_of(session, route.id)}
    drop_of = {r.order_id: stops[s] for s, found in rows.items() for r in found if s != stop.id}
    mine = {r.order_id: r for r in rows[stop.id]}
    answered = {o.order_id for o in ready.orders}
    collected, already, not_ready = [], [], []
    for order_id, row in mine.items():
        order = await orders_api.get(session, order_id, for_update=True)  # read again, locked
        if order_id in answered and order.status == "packed" and order_id in drop_of:
            await _dispatch(session, route, order, row, drop_of[order_id])
            collected.append(
                CollectedTiffin(
                    order_id=order_id, short_code=row.short_code, drop_seq=drop_of[order_id].seq
                )
            )
        elif order.status == "dispatched":
            attempt = await attempts.latest(session, order_id)
            if attempt is not None and attempt.stop_id in stops:
                already.append(order_id)
        elif order.status in NOT_YET:
            not_ready.append(WaitingTiffin(order_id=order_id, short_code=row.short_code))
    not_on_route = sorted(answered - set(mine))
    if not collected and not already:  # the kitchen has not tapped Ready yet: nothing changes
        raise conflict(
            "logistics.nothing_ready",
            "Nothing is ready at this kitchen yet; scan again when it is.",
            not_ready=[w.model_dump(mode="json") for w in not_ready],
        )
    if collected:
        at = now()
        if stop.status != "handed_over":
            stop.status, stop.handed_over_at = "handed_over", at
        stop.arrived_at = stop.arrived_at or at
        stop.bags = len(collected) + len(already)
        execution.tap(
            session,
            route,
            "collect",
            who,
            body.client_at,
            stop=stop,
            payload={
                "assignment_id": str(stop.assignment_id),
                "collected": len(collected),
                "not_ready": len(not_ready),
            },
        )
        await session.flush()
    return collected, already, not_ready, not_on_route


async def _dispatch(
    session: AsyncSession,
    route: Route,
    order: orders_api.Order,
    row: StopOrder,
    drop: RouteStop,
) -> None:
    """packed → dispatched as the route's rider (M8 pays him for it), and attempt 1 on its drop."""
    await orders_api.transition(
        session, order.id, "dispatched", actor="rider", actor_user_id=route.rider_user_id
    )
    session.add(
        Delivery(
            order_id=order.id,
            attempt=1,
            hub_id=order.hub_id,
            society_id=order.society_id,
            order_kind=order.kind,
            delivery_date=order.delivery_date,
            slot_id=order.slot_id,
            stop_id=drop.id,
            status="on_route",
            short_code=row.short_code,
            delivery_code=attempts.new_code(),
        )
    )
    await session.flush()


async def hand_over(
    session: AsyncSession,
    valkey: Redis | None,
    route: Route,
    stop: RouteStop,
    actor: Actor,
    body: HandoverIn,
    proof: str,
) -> tuple[handover.Handover, list[uuid.UUID]]:
    """A captain's drop (§3.7): she counts the tiffins still with the rider (no crates); matching
    counts make their attempts `with_captain`, with no M4 move."""
    if stop.kind != "drop" or actor.kind == "rider" or body.scanned_bags:
        raise ProblemError(
            422,
            "request.invalid",
            "A tiffin drop is handed over by count to its captain, without scanned bags; the "
            "rider delivers rider-mode drops door by door.",
        )
    riding = list(
        (
            await session.execute(
                select(Delivery)
                .where(Delivery.stop_id == stop.id, Delivery.status == "on_route")
                .order_by(Delivery.short_code)
                .with_for_update()
            )
        ).scalars()
    )
    if (body.crates, body.bags) != (0, len(riding)):
        raise conflict(
            "logistics.count_mismatch",
            "The count does not match the tiffins the rider carries for this society.",
            expected={"crates": 0, "bags": len(riding)},
            short_codes=[d.short_code for d in riding],
        )
    moved = [d.order_id for d in riding]
    for delivery in riding:
        delivery.status, delivery.at = "with_captain", now()
        delivery.by_actor, delivery.user_id = actor.kind, actor.user_id
    made = await handover.record(
        session, valkey, route, stop, actor, body, proof, (0, len(riding)), moved
    )
    return made, moved


async def close(
    session: AsyncSession, route: Route, actor: str, actor_user: uuid.UUID | None
) -> int:
    """Complete a started tiffin route (§3.7). The rider's complete: tiffins still with him become
    `undelivered` (`returned_to_hub`) and are refunded at once. `close_routes` (actor `system`)
    moves none: its `sweep` sends them to triage (`not_confirmed`). Either way tiffins never
    collected get attempt 1 in triage (`left_behind`), and a captain's are left to her taps or
    the 16:00 sweep.
    Returns the number of tiffins refunded."""
    rows = await _rows(session, route)
    stops = {s.id: s for s in await stops_of(session, route.id)}
    back = 0
    on_route = [] if actor == "system" else await _out(session, list(stops), ("on_route",))
    for delivery in on_route:  # `close_routes` leaves them to `sweep`, which moves no order
        order = await orders_api.get(session, delivery.order_id, for_update=True)
        if order.status != "dispatched":
            continue
        await orders_api.transition(
            session,
            order.id,
            "undelivered",
            actor=actor,
            actor_user_id=actor_user,
            payload={"reason": "returned_to_hub"},
        )
        delivery.status, delivery.reason, delivery.at = "undelivered", "returned_to_hub", now()
        delivery.by_actor, delivery.user_id = actor, actor_user
        await deliveries.refund_in_full(session, delivery, order.id, "returned_to_hub")
        back += 1
    drop_of = {r.order_id: s for s, found in rows.items() for r in found if stops[s].kind == "drop"}
    for kitchen in (s for s in stops.values() if s.kind == "kitchen"):
        for row in rows[kitchen.id]:
            if await attempts.latest(session, row.order_id) is not None:
                continue
            order = await orders_api.get(session, row.order_id, for_update=True)
            if order.status in NOT_YET:
                _left_behind(session, order, row, drop_of.get(row.order_id))
    await session.flush()
    await execution.finish(session, route)
    return back


def _left_behind(
    session: AsyncSession, order: orders_api.Order, row: StopOrder, drop: uuid.UUID | None
) -> None:
    session.add(
        Delivery(
            order_id=order.id,
            attempt=1,
            hub_id=order.hub_id,
            society_id=order.society_id,
            order_kind=order.kind,
            delivery_date=order.delivery_date,
            slot_id=order.slot_id,
            stop_id=drop,
            status="triage",
            reason="left_behind",
            by_actor="system",
            short_code=row.short_code,
            delivery_code=attempts.new_code(),
        )
    )


def _to_triage(delivery: Delivery) -> None:
    delivery.status, delivery.reason, delivery.at = "triage", "not_confirmed", now()
    delivery.by_actor, delivery.user_id = "system", None


async def _out(
    session: AsyncSession, stop_ids: list[uuid.UUID], statuses: tuple[str, ...]
) -> list[Delivery]:
    found = await session.execute(
        select(Delivery)
        .where(
            Delivery.stop_id.in_(stop_ids),
            Delivery.order_kind == "tiffin",
            Delivery.status.in_(statuses),
        )
        .order_by(Delivery.id)
        .with_for_update()
    )
    return list(found.scalars())


async def sweep(session: AsyncSession, day: date) -> int:
    """16:00 (`close_routes`): every tiffin of the day, or of an earlier day, still with a rider
    or a captain goes to triage (`not_confirmed`); the hub settles it (§3.7)."""
    found = await session.execute(
        select(Delivery)
        .where(
            Delivery.order_kind == "tiffin",
            Delivery.delivery_date <= day,
            Delivery.status.in_(OUT),
        )
        .order_by(Delivery.id)
        .with_for_update(skip_locked=True)
    )
    swept = list(found.scalars())
    for delivery in swept:
        _to_triage(delivery)
    await session.flush()
    return len(swept)
