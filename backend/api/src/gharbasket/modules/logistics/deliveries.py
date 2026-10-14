"""The doors (spec §3.4): proof of delivery on the order's current attempt.

Attempt 1 is opened when the order's route starts (with the household's delivery code) and
follows the handover (`with_captain`). At the door the bag's QR must be the order's; a captain may
prove it by the scan alone, a rider needs the household's code or a photo. Order moves go through
`orders.api.transition`.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.attempts import latest
from gharbasket.modules.logistics.handover import (
    MAX_CODE_TRIES,
    Actor,
    CodeInvalid,
    ensure_captain,
    photo_ok,
)
from gharbasket.modules.logistics.models import Delivery, FieldEvent, Route, RouteStop
from gharbasket.modules.logistics.routes import caller, conflict
from gharbasket.modules.logistics.schemas import ProofIn
from gharbasket.modules.logistics.views import now
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit

CLOSED = ("delivered", "refunded")


def delivery_not_found() -> ProblemError:
    return ProblemError(404, "logistics.delivery_not_found", "No such delivery.")


async def door_actor(
    session: AsyncSession, permit: Permit, order_id: uuid.UUID
) -> tuple[Delivery, orders_api.Order, Actor]:
    """The order's current attempt and who acts at its door: a hub manager of its hub, the rider
    of its rider-mode stop while the route runs, or its society's active captain."""
    delivery = await latest(session, order_id, for_update=True)
    if delivery is None:
        raise delivery_not_found()
    order = await orders_api.get(session, order_id, for_update=True)
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


def check_proof(delivery: Delivery, order: orders_api.Order, actor: Actor, body: ProofIn) -> None:
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
        if body.photo_key is None or not photo_ok(body.photo_key, actor.user_id):
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
