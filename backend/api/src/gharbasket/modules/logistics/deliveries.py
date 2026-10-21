"""The doors (spec §3.4): proof of delivery, undelivered, hold and triage, one `Delivery` per
attempt.

Attempt 1 is opened when the order's route starts (with the household's delivery code), follows
the handover (`with_captain`) and ends delivered, held (a captain's `not_home`, until 20:00 IST),
undelivered, refunded or in triage. Order moves go through `orders.api.transition`; refunds go
through M4's move to `refunded`, which pays the wallet (ADR-0027 §5). A captain may prove a door
by the bag's scan alone; a rider needs the household's code or a photo.
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, time, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.logistics import events
from gharbasket.modules.logistics.attempts import latest, new_code, orphaned
from gharbasket.modules.logistics.handover import (
    MAX_CODE_TRIES,
    Actor,
    CodeInvalid,
    ensure_captain,
    keep_photo,
)
from gharbasket.modules.logistics.models import Delivery, FieldEvent, Route, RoutePlan, RouteStop
from gharbasket.modules.logistics.routes import caller, conflict
from gharbasket.modules.logistics.schemas import ProofIn, ResolveIn, UndeliveredIn
from gharbasket.modules.logistics.views import hhmm, now
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.permissions import Permit

log = structlog.get_logger("gharbasket.logistics")
HOLD_UNTIL = time(20, 0)  # IST; hold_expiry runs at 20:30
REFUND_AT_ONCE = ("damaged", "missing")  # our fault: refunded in full
TRIAGE = ("refused", "wrong_address", "other")
CLOSED = ("delivered", "refunded")


def delivery_not_found() -> ProblemError:
    return ProblemError(404, "logistics.delivery_not_found", "No such delivery.")


async def door_actor(
    session: AsyncSession, permit: Permit, order_id: uuid.UUID
) -> tuple[Delivery, orders_api.Order, Actor]:
    """The order's current attempt and who acts at its door: a hub manager of its hub, the rider
    of its rider-mode stop while the route runs, or its society's active captain."""
    if await latest(session, order_id) is None:
        raise delivery_not_found()
    # One lock order everywhere: the order, then its attempt.
    order = await orders_api.get(session, order_id, for_update=True)
    delivery = await latest(session, order_id, for_update=True)
    assert delivery is not None
    who = caller(permit, order.hub_id)
    user_id = permit.principal.user_id
    if who.manager:
        return delivery, order, Actor("hub", user_id)
    stop = None if delivery.stop_id is None else await session.get(RouteStop, delivery.stop_id)
    route = None if stop is None else await session.get(Route, stop.route_id)
    riding = stop is not None and route is not None and route.rider_user_id == user_id
    if who.rider and riding and stop.rider_mode and route.status == "in_progress":  # pyright: ignore[reportOptionalMemberAccess]
        return delivery, order, Actor("rider", user_id)
    if any(g.role == "captain" and g.scope_id == order.society_id for g in permit.grants):
        partner = await ensure_captain(session, user_id, order.society_id)
        return delivery, order, Actor("captain", user_id, partner)
    raise delivery_not_found()


def _ensure_open(delivery: Delivery, order: orders_api.Order) -> None:
    if order.status in CLOSED or delivery.status in CLOSED:
        raise conflict("logistics.delivery_closed", f"This delivery is {order.status}.")


def check_proof(
    delivery: Delivery, order: orders_api.Order, actor: Actor, body: ProofIn, photo_valid: bool
) -> None:
    """The bag is this order's and the proof fits the deliverer; a wrong code is counted (the
    caller commits the count before answering)."""
    _ensure_open(delivery, order)
    if body.bag_qr != f"GB1:{order.id.hex}":
        raise ProblemError(422, "logistics.bag_mismatch", "This bag is not this order's.")
    if body.proof == "scan":
        if actor.kind == "rider":
            raise ProblemError(
                422, "logistics.proof_required", "A rider needs the household's code or a photo."
            )
        return
    if body.proof == "photo":
        if not photo_valid:
            raise ProblemError(
                422, "logistics.photo_key_invalid", "Upload the photo as delivery_photo first."
            )
        return
    if delivery.code_attempts >= MAX_CODE_TRIES:
        raise ProblemError(
            429,
            "logistics.code_attempts_exceeded",
            "Too many wrong codes for this delivery; take a photo instead.",
        )
    if body.delivery_code != delivery.delivery_code:
        delivery.code_attempts += 1
        raise CodeInvalid("That is not the household's delivery code.")


async def _tap(
    session: AsyncSession,
    delivery: Delivery,
    action: str,
    actor: Actor,
    payload: dict[str, object] | None,
    client_at: object,
) -> None:
    stop = None if delivery.stop_id is None else await session.get(RouteStop, delivery.stop_id)
    if stop is not None:
        session.add(
            FieldEvent(
                route_id=stop.route_id,
                stop_id=stop.id,
                order_id=delivery.order_id,
                action=action,
                user_id=actor.user_id,
                payload=payload,
                client_at=client_at,
            )
        )


async def deliver(
    session: AsyncSession, delivery: Delivery, order: orders_api.Order, actor: Actor, body: ProofIn
) -> None:
    """at_society → delivered (a held bag too, until hold_expiry). Only a photo proof keeps the
    photo: claimed for M7 and deleted 90 days after the delivery date."""
    await orders_api.transition(
        session, order.id, "delivered", actor=actor.kind, actor_user_id=actor.user_id
    )
    photo = body.photo_key if body.proof == "photo" else None
    if photo is not None:
        await keep_photo(session, photo, actor.user_id, delivery.delivery_date)
    delivery.status, delivery.proof, delivery.photo_key = "delivered", body.proof, photo
    delivery.by_actor, delivery.user_id, delivery.client_at = (
        actor.kind,
        actor.user_id,
        body.client_at,
    )
    delivery.hold_until, delivery.at = None, now()
    await _tap(session, delivery, "proof", actor, {"proof": body.proof}, body.client_at)
    await session.flush()


async def refund(
    session: AsyncSession,
    delivery: Delivery,
    order_id: uuid.UUID,
    amount: int,
    reason: str,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    """undelivered → refunded (actor system; a manager's triage names the manager); M4 pays the
    wallet."""
    await orders_api.transition(
        session,
        order_id,
        "refunded",
        actor="system",
        actor_user_id=actor_user_id,
        payload={"refund_paise": amount, "reason": reason},
    )
    delivery.status, delivery.refund_paise, delivery.at = "refunded", amount, now()


async def try_refund(
    session: AsyncSession,
    delivery: Delivery,
    order_id: uuid.UUID,
    amount: int,
    reason: str,
    actor_user_id: uuid.UUID | None = None,
) -> bool:
    """`refund` in a savepoint; False (nothing changed) when M5 refuses it because the wallet would
    go over its cap (`billing.wallet_limit`), so the caller can leave the order for triage."""
    try:
        async with session.begin_nested():
            await refund(session, delivery, order_id, amount, reason, actor_user_id)
    except ProblemError as exc:
        if exc.code != "billing.wallet_limit":
            raise
        await session.refresh(delivery)
        return False
    return True


async def fail(
    session: AsyncSession,
    delivery: Delivery,
    order: orders_api.Order,
    actor: Actor,
    body: UndeliveredIn,
) -> None:
    """The table of §3.4: a captain holds a not-home bag until 20:00; otherwise the order is
    undelivered, then refunded at once (damaged, missing) or left for triage."""
    _ensure_open(delivery, order)
    if body.reason == "not_home" and actor.kind == "captain":
        # Only a bag in her hands today: not a replay after hold_expiry, not a triage attempt.
        if order.status != "at_society" or delivery.status not in ("with_captain", "held"):
            raise conflict(
                "logistics.delivery_closed",
                f"This bag cannot be held now ({delivery.status}).",
            )
        if delivery.status != "held":
            delivery.status, delivery.hold_until, delivery.reason = "held", HOLD_UNTIL, "not_home"
            delivery.by_actor, delivery.user_id, delivery.at = "captain", actor.user_id, now()
            await publish(
                session,
                events.DELIVERY_HELD,
                {
                    "order_id": str(order.id),
                    "household_id": str(order.household_id),
                    "society_id": str(order.society_id),
                    "hold_until": hhmm(HOLD_UNTIL),
                },
            )
            await _tap(session, delivery, "hold", actor, None, body.client_at)
        return
    at_society = order.status == "at_society"
    await orders_api.transition(
        session,
        order.id,
        "undelivered",
        actor=actor.kind,
        actor_user_id=actor.user_id,
        payload={"reason": body.reason},
    )
    if body.photo_key is not None:
        await keep_photo(session, body.photo_key, actor.user_id, delivery.delivery_date)
    delivery.status, delivery.reason, delivery.hold_until = "undelivered", body.reason, None
    delivery.by_actor, delivery.user_id, delivery.note = actor.kind, actor.user_id, body.note
    delivery.photo_key, delivery.client_at, delivery.at = body.photo_key, body.client_at, now()
    # A bag that ended at the society with the captain stays with her (a rider's rides back).
    delivery.kept_by_captain = at_society and not await _rider_holds(session, delivery)
    if body.reason in REFUND_AT_ONCE:
        amount = await orders_api.refundable_paise(session, order.id)
        if not await try_refund(session, delivery, order.id, amount, body.reason):
            delivery.status = "triage"  # the wallet is full: the hub refunds to the source
    elif body.reason in TRIAGE:
        delivery.status = "triage"
    await _tap(session, delivery, "undelivered", actor, {"reason": body.reason}, body.client_at)
    await session.flush()


async def _rider_holds(session: AsyncSession, delivery: Delivery) -> bool:
    """Whether the stop is in rider mode: the bag at the society is the rider's, not a captain's."""
    stop = None if delivery.stop_id is None else await session.get(RouteStop, delivery.stop_id)
    return stop is not None and stop.rider_mode


async def redelivery_day(session: AsyncSession, hub_id: uuid.UUID, earliest: date) -> date:
    """The first day from `earliest` with no box plan solving, solved or published (M3 review)."""
    day = earliest
    for _ in range(14):
        taken = await session.scalar(
            select(RoutePlan.id)
            .where(
                RoutePlan.hub_id == hub_id,
                RoutePlan.delivery_date == day,
                RoutePlan.kind == "box",
                RoutePlan.status.in_(("solving", "solved", "published")),
            )
            .limit(1)
        )
        if taken is None:
            return day
        day += timedelta(days=1)
    return day


async def schedule_redelivery(
    session: AsyncSession,
    delivery: Delivery,
    order: orders_api.Order,
    day: date,
    *,
    actor: str,
    actor_user_id: uuid.UUID | None,
) -> Delivery:
    """The one free redelivery (§3.4): undelivered → redelivery and attempt 2 on the first day
    from `day` whose plan is not solved yet (a plan already solved would never carry it), in the
    same slot's window; that day's 23:15 solve gives the society a stop for it."""
    day = await redelivery_day(session, order.hub_id, day)
    await orders_api.transition(
        session, order.id, "redelivery", actor=actor, actor_user_id=actor_user_id
    )
    delivery.status, delivery.at = "redelivery", now()
    again = Delivery(
        order_id=order.id,
        attempt=2,
        hub_id=order.hub_id,
        society_id=order.society_id,
        order_kind=order.kind,
        delivery_date=day,
        slot_id=order.slot_id,
        status="scheduled",
        delivery_code=new_code(),
        kept_by_captain=delivery.kept_by_captain,  # a bag in her hands stays there overnight
    )
    try:
        async with session.begin_nested():
            session.add(again)
            await session.flush()
    except IntegrityError:  # two resolves at once: the other one booked it
        raise conflict(
            "logistics.not_undelivered", "This order was resolved by someone else just now."
        ) from None
    await announce_redelivery(session, order, day)
    return again


async def announce_redelivery(session: AsyncSession, order: orders_api.Order, day: date) -> None:
    """Tell the household (M11) the date and window of the redelivery."""
    slot = await orders_api.slot_ref(session, order.slot_id)
    await publish(
        session,
        events.REDELIVERY_SCHEDULED,
        {
            "order_id": str(order.id),
            "household_id": str(order.household_id),
            "society_id": str(order.society_id),
            "delivery_date": day.isoformat(),
            "window": None if slot is None else {"start": hhmm(slot.start), "end": hhmm(slot.end)},
        },
    )


async def book_ride(
    session: AsyncSession, delivery: Delivery, order: orders_api.Order, day: date
) -> None:
    """A packed bag the hub's route left behind gets a ride on the next unsolved day (no order
    move: the route start takes it from packed to dispatched). The ride is attempt 1 booked
    again, flagged `hub_ride`: it is no customer miss, so a real miss on it still gets the one
    free redelivery."""
    delivery.status, delivery.reason, delivery.at = "scheduled", None, now()
    delivery.hub_ride, delivery.stop_id = True, None
    delivery.delivery_date = await redelivery_day(session, order.hub_id, day)
    delivery.delivery_code = new_code()
    delivery.code_attempts = 0
    await session.flush()


async def rebook(
    session: AsyncSession, delivery: Delivery, order: orders_api.Order, day: date
) -> None:
    """A redelivery no route picked up, booked again on the first day from `day` whose plan is not
    solved yet; the household is told the new date (a left-behind ride was never announced)."""
    delivery.delivery_date = await redelivery_day(session, order.hub_id, day)
    delivery.at = now()
    await session.flush()
    if not delivery.hub_ride:
        await announce_redelivery(session, order, delivery.delivery_date)


async def resolve(
    session: AsyncSession,
    delivery: Delivery,
    order: orders_api.Order,
    user_id: uuid.UUID,
    body: ResolveIn,
    tomorrow: date,
) -> None:
    """A hub manager's triage. An undelivered order: the one free redelivery (attempt 1 only), or
    a refund of up to what is left of the order's paid total (actor system on M4's move, naming
    the manager; also audited). A packed bag the day's route left behind can only ride again
    (`book_ride`). A redelivery no route picked up (an orphan) is booked again on the next
    solvable day, or refunded in full (its order moves `redelivery → refunded`, ADR-0028)."""
    left_behind = (
        delivery.status == "triage"
        and delivery.reason == "left_behind"
        and order.status == "packed"
    )
    orphan = await orphaned(session, delivery, tomorrow - timedelta(days=1))
    if (left_behind or (orphan and order.status == "packed")) and body.outcome != "redeliver":
        raise conflict("logistics.not_undelivered", "A left-behind bag can only ride again.")
    if not (left_behind or orphan) and (
        order.status != "undelivered" or delivery.status not in ("undelivered", "triage")
    ):
        raise conflict("logistics.not_undelivered", "Only an undelivered order is resolved.")
    amount = None
    if left_behind:
        await book_ride(session, delivery, order, tomorrow)
    elif orphan and body.outcome == "redeliver":
        await rebook(session, delivery, order, tomorrow)
    elif body.outcome == "redeliver":
        if delivery.attempt != 1:
            raise ProblemError(
                422, "request.invalid", "The free redelivery was used; refund instead."
            )
        await schedule_redelivery(
            session, delivery, order, tomorrow, actor="hub", actor_user_id=user_id
        )
    else:
        most = await orders_api.refundable_paise(session, order.id)
        amount = most if body.refund_paise is None else body.refund_paise
        if amount > most:
            raise ProblemError(
                422,
                "logistics.refund_invalid",
                "That is more than is left to refund on this order.",
                extra={"max_paise": most},
            )
        if amount < most and not body.final:  # `refunded` is final: the rest could never go back
            raise ProblemError(
                422,
                "logistics.partial_refund_not_final",
                "A refund of less than all that is left ends the order; send final: true to "
                "keep the rest unrefunded.",
                extra={"max_paise": most},
            )
        await refund(session, delivery, order.id, amount, "triage", user_id)
    delivery.note, delivery.by_actor, delivery.user_id = body.note, "hub", user_id
    await audit.record(
        session,
        actor_user_id=user_id,
        action="deliveries.resolved",
        entity_type="order",
        entity_id=str(order.id),
        after={"outcome": body.outcome, "refund_paise": amount, "note": body.note},
    )
    await session.flush()


# --- the evening jobs (§6) ----------------------------------------------------------------------

SECOND_MISS_FEE_PAISE = 2_000  # ₹20 (spec §11)
MISSED = ("not_home", "returned_to_hub")


async def _due(
    session: AsyncSession, day: date, statuses: tuple[str, ...], *, since: bool = False
) -> list[uuid.UUID]:
    """The attempts of `day` in these statuses; with `since`, those of earlier days too."""
    when = Delivery.delivery_date <= day if since else Delivery.delivery_date == day
    rows = await session.execute(
        select(Delivery.id).where(when, Delivery.status.in_(statuses)).order_by(Delivery.id)
    )
    return list(rows.scalars())


async def _locked(
    session: AsyncSession, attempt_id: uuid.UUID
) -> tuple[orders_api.Order, Delivery]:
    """The order, then the attempt, locked in the one order every path uses; a tap in flight is
    waited for, never skipped."""
    found = await session.get(Delivery, attempt_id)
    assert found is not None
    order = await orders_api.get(session, found.order_id, for_update=True)
    attempt = await session.get(Delivery, attempt_id, with_for_update=True, populate_existing=True)
    assert attempt is not None
    return order, attempt


async def _each(
    session: AsyncSession,
    ids: list[uuid.UUID],
    handle: Callable[[uuid.UUID], Awaitable[str | None]],
    counts: dict[str, int],
) -> dict[str, int]:
    """`handle` per attempt in its own savepoint: one order's error is counted and leaves the
    others' moves in place."""
    counts["failed"] = 0
    for attempt_id in ids:
        try:
            async with session.begin_nested():
                outcome = await handle(attempt_id)
        except Exception as exc:
            log.error("delivery_job_failed", attempt_id=str(attempt_id), error=type(exc).__name__)
            counts["failed"] += 1
            continue
        if outcome is not None:
            counts[outcome] += 1
    await session.flush()
    return counts


async def expire_holds(session: AsyncSession, day: date) -> dict[str, int]:
    """T 20:30: every order of the day, and of earlier days whose run was missed, still at the
    society becomes undelivered (actor system):
    `not_home` for a held bag, which stays with the captain overnight, `not_confirmed` (to triage)
    for one never scanned at a door. Orders the day's routes left behind and that never rode go
    to triage too (`left_behind`)."""

    async def expire(attempt_id: uuid.UUID) -> str | None:
        order, attempt = await _locked(session, attempt_id)
        if order.status != "at_society" or attempt.status not in ("with_captain", "held"):
            return None
        reason = "not_home" if attempt.status == "held" else "not_confirmed"
        await orders_api.transition(
            session, order.id, "undelivered", actor="system", payload={"reason": reason}
        )
        attempt.status = "undelivered" if reason == "not_home" else "triage"
        attempt.reason, attempt.by_actor, attempt.user_id = reason, "system", None
        # In her hands when the hold ended (a rider-mode stop's bag is the rider's): the next
        # morning's van carries nothing for it, whatever the reason.
        attempt.kept_by_captain = not await _rider_holds(session, attempt)
        attempt.hold_until, attempt.at = None, now()
        return reason

    counts = await _each(
        session,
        await _due(session, day, ("with_captain", "held"), since=True),
        expire,
        {"not_home": 0, "not_confirmed": 0},
    )
    counts["left_behind"] = await _sweep_left_behind(session, day)
    return counts


async def _sweep_left_behind(session: AsyncSession, day: date) -> int:
    """An attempt in triage for each order the day's routes left behind that never rode."""
    routes = await session.execute(
        select(Route.left_behind).where(Route.delivery_date == day, Route.left_behind != [])
    )
    swept = 0
    for order_id in sorted({o for ids in routes.scalars() for o in ids}):
        if await latest(session, order_id) is not None:
            continue
        order = await orders_api.get(session, order_id, for_update=True)
        if order.status not in ("locked", "picking", "packed"):
            continue
        session.add(
            Delivery(
                order_id=order.id,
                attempt=1,
                hub_id=order.hub_id,
                society_id=order.society_id,
                order_kind=order.kind,
                delivery_date=day,
                slot_id=order.slot_id,
                status="triage",
                reason="left_behind",
                by_actor="system",
                delivery_code=new_code(),
            )
        )
        swept += 1
    await session.flush()
    return swept


async def second_miss_refund(
    session: AsyncSession, order: orders_api.Order
) -> tuple[int, list[dict[str, object]]]:
    """A box missed twice (ADR-0027 §2, the owner's decision of 9 Oct): the perishable lines'
    delivered steps at the paid price minus ₹20, never below 0 and never more than is left to
    refund. The non-perishable lines are not refunded: they ride on the household's next delivery.
    Returns the amount and those lines, which the caller records."""
    [summary] = await orders_api.summaries(session, [order.id])
    fresh = await catalog_api.perishable_ids(session, [ln.product_id for ln in summary.lines])
    perishable = 0
    kept: list[dict[str, object]] = []
    for ln in summary.lines:
        steps = ln.qty - ln.shortage_qty
        if ln.product_id in fresh:
            perishable += steps * ln.unit_price_paise
        elif steps > 0:
            kept.append(
                {
                    "product_id": str(ln.product_id),
                    "steps": steps,
                    "paid_paise": steps * ln.unit_price_paise,
                }
            )
    most = await orders_api.refundable_paise(session, order.id)
    return min(max(0, perishable - SECOND_MISS_FEE_PAISE), most), kept


async def schedule_day(session: AsyncSession, day: date) -> dict[str, int]:
    """T 20:45: the not-home and returned bags (box, add-on) of the day and of earlier days that
    never got a decision (a route `close_routes` completed late). A first miss gets the one free
    redelivery on the next day whose plan is not solved; a second miss is refunded
    (`second_miss_refund`: the perishables minus the fee, the non-perishables carried on and
    recorded), or goes to triage when the wallet cannot take it."""

    async def settle(attempt_id: uuid.UUID) -> str | None:
        order, attempt = await _locked(session, attempt_id)
        missed = attempt.status == "undelivered" and attempt.reason in MISSED
        if (
            not missed
            or attempt.order_kind not in ("box", "addon")
            or order.status != "undelivered"
        ):
            return None
        if attempt.attempt == 1:
            await schedule_redelivery(
                session, attempt, order, day + timedelta(days=1), actor="system", actor_user_id=None
            )
            return "redelivery"
        amount, kept = await second_miss_refund(session, order)
        if not await try_refund(session, attempt, order.id, amount, "second_miss"):
            attempt.status = "triage"
            return "triage"
        stop = None if attempt.stop_id is None else await session.get(RouteStop, attempt.stop_id)
        if stop is not None:
            session.add(
                FieldEvent(
                    route_id=stop.route_id,
                    stop_id=stop.id,
                    order_id=order.id,
                    action="resolve",
                    user_id=None,
                    payload={"second_miss": True, "refund_paise": amount, "non_perishable": kept},
                )
            )
        if kept:  # M4 keeps them for the next box (M4 §3.8); the event stays M7's record
            await orders_api.carry_over(session, order.id, kept)
            await publish(
                session,
                events.CARRY_OVER_PENDING,
                {
                    "order_id": str(order.id),
                    "household_id": str(order.household_id),
                    "hub_id": str(order.hub_id),
                    "society_id": str(order.society_id),
                    "lines": kept,
                },
            )
        return "refunded"

    return await _each(
        session,
        await _due(session, day, ("undelivered",), since=True),
        settle,
        {"redelivery": 0, "refunded": 0, "triage": 0},
    )
