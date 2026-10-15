"""The doors (spec §3.4): proof of delivery, undelivered, hold and triage, one `Delivery` per
attempt.

Attempt 1 is opened when the order's route starts (with the household's delivery code), follows
the handover (`with_captain`) and ends delivered, held (a captain's `not_home`, until 20:00 IST),
undelivered, refunded or in triage. Order moves go through `orders.api.transition`; refunds go
through M4's move to `refunded`, which pays the wallet (ADR-0027 §5). A captain may prove a door
by the bag's scan alone; a rider needs the household's code or a photo.
"""

import uuid
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import events
from gharbasket.modules.logistics.attempts import latest, new_code
from gharbasket.modules.logistics.handover import (
    MAX_CODE_TRIES,
    Actor,
    CodeInvalid,
    ensure_captain,
)
from gharbasket.modules.logistics.models import Delivery, FieldEvent, Route, RouteStop
from gharbasket.modules.logistics.routes import caller, conflict
from gharbasket.modules.logistics.schemas import ProofIn, ResolveIn, UndeliveredIn
from gharbasket.modules.logistics.views import hhmm, now
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.permissions import Permit

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
    """at_society → delivered (a held bag too, until hold_expiry)."""
    await orders_api.transition(
        session, order.id, "delivered", actor=actor.kind, actor_user_id=actor.user_id
    )
    delivery.status, delivery.proof, delivery.photo_key = "delivered", body.proof, body.photo_key
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
    await orders_api.transition(
        session,
        order.id,
        "undelivered",
        actor=actor.kind,
        actor_user_id=actor.user_id,
        payload={"reason": body.reason},
    )
    delivery.status, delivery.reason, delivery.hold_until = "undelivered", body.reason, None
    delivery.by_actor, delivery.user_id, delivery.note = actor.kind, actor.user_id, body.note
    delivery.photo_key, delivery.client_at, delivery.at = body.photo_key, body.client_at, now()
    if body.reason in REFUND_AT_ONCE:
        amount = await orders_api.refundable_paise(session, order.id)
        if not await try_refund(session, delivery, order.id, amount, body.reason):
            delivery.status = "triage"  # the wallet is full: the hub refunds to the source
    elif body.reason in TRIAGE:
        delivery.status = "triage"
    await _tap(session, delivery, "undelivered", actor, {"reason": body.reason}, body.client_at)
    await session.flush()


async def schedule_redelivery(
    session: AsyncSession,
    delivery: Delivery,
    order: orders_api.Order,
    day: date,
    *,
    actor: str,
    actor_user_id: uuid.UUID | None,
) -> Delivery:
    """The one free redelivery (§3.4): undelivered → redelivery and attempt 2 on `day`, in the
    same slot's window; T+1's 23:15 solve gives the society a stop for it."""
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
    )
    session.add(again)
    await session.flush()
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
    return again


async def resolve(
    session: AsyncSession,
    delivery: Delivery,
    order: orders_api.Order,
    user_id: uuid.UUID,
    body: ResolveIn,
    tomorrow: date,
) -> None:
    """A hub manager's triage: the one free redelivery (attempt 1 only), or a refund of up to
    what is left of the order's paid total (actor system on M4's move; the manager is audited)."""
    if order.status != "undelivered" or delivery.status not in ("undelivered", "triage"):
        raise conflict("logistics.not_undelivered", "Only an undelivered order is resolved.")
    amount = None
    if body.outcome == "redeliver":
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
        await refund(session, delivery, order.id, amount, "triage")
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
