"""The customer's delivery card (spec §3.5) and the hub's exceptions board (§3.4).

The household sees its order's attempt: the window, the live ETA and the route to track while the
order rides the van, the captain's card (never her phone or flat), the hold, the delivery code
(only while the order is dispatched or at the society) and the door photo as a 5-minute link.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.logistics.attempts import latest
from gharbasket.modules.logistics.models import Delivery, Route, RouteStop
from gharbasket.modules.logistics.schemas import (
    CaptainOut,
    DeliveryCard,
    ProofView,
    Redelivery,
    Window,
)
from gharbasket.modules.logistics.views import eta_now, hhmm, stops_of
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.files import ObjectStore


async def _stop(
    session: AsyncSession, order: orders_api.Order, attempt: Delivery | None
) -> tuple[RouteStop, Route] | None:
    """The stop the attempt rides on, or before the start the published plan's stop."""
    if attempt is not None and attempt.stop_id is not None:
        stop = await session.get(RouteStop, attempt.stop_id)
        route = None if stop is None else await session.get(Route, stop.route_id)
        return None if stop is None or route is None else (stop, route)
    day = order.delivery_date if attempt is None else attempt.delivery_date
    found = await session.execute(
        select(RouteStop, Route)
        .join(Route, Route.id == RouteStop.route_id)
        .where(
            Route.delivery_date == day,
            Route.status.in_(("published", "in_progress")),
            RouteStop.society_id == order.society_id,
            RouteStop.slot_id == order.slot_id,
        )
        .limit(1)
    )
    row = found.first()
    return None if row is None else (row[0], row[1])


async def delivery_card(
    session: AsyncSession,
    order: orders_api.Order,
    *,
    household: bool,
    store: ObjectStore | None,
) -> DeliveryCard:
    attempt = await latest(session, order.id)
    slot = await orders_api.slot_ref(session, order.slot_id)
    window = None if slot is None else Window(start=hhmm(slot.start), end=hhmm(slot.end))
    eta = tracking = None
    placed = await _stop(session, order, attempt)
    if placed is not None:
        stop, route = placed
        live = eta_now(route, await stops_of(session, route.id))[stop.id]
        eta = live or (hhmm(stop.eta) if stop.status in ("pending", "arrived") else None)
        if order.status == "dispatched" and route.status == "in_progress":
            tracking = {"route_id": route.id}
    card = await community_api.captain_card(session, order.society_id)
    proof = None
    if attempt is not None and attempt.proof is not None:
        url = None
        if attempt.photo_key is not None and store is not None:
            url = store.signed_get(attempt.photo_key)
        proof = ProofView(kind=attempt.proof, at=attempt.at, photo_url=url)  # pyright: ignore[reportArgumentType]
    showing_code = (
        household and attempt is not None and order.status in ("dispatched", "at_society")
    )
    return DeliveryCard(
        order_id=order.id,
        status="scheduled" if attempt is None else attempt.status,  # pyright: ignore[reportArgumentType]
        attempt=1 if attempt is None else attempt.attempt,
        window=window,
        eta=eta,
        tracking=tracking,
        captain=None if card is None else CaptainOut(**vars(card)),
        hold_until=None
        if attempt is None or attempt.hold_until is None
        else hhmm(attempt.hold_until),
        delivery_code=attempt.delivery_code if showing_code and attempt else None,
        proof=proof,
        redelivery=None
        if attempt is None or attempt.attempt != 2
        else Redelivery(date=attempt.delivery_date, window=window),
    )


async def board(
    session: AsyncSession,
    hub_id: uuid.UUID,
    day: date,
    status: str | None,
    after: uuid.UUID | None,
    limit: int,
) -> list[Delivery]:
    stmt = select(Delivery).where(Delivery.hub_id == hub_id, Delivery.delivery_date == day)
    if status is not None:
        stmt = stmt.where(Delivery.status == status)
    if after is not None:
        stmt = stmt.where(Delivery.id > after)
    return list((await session.execute(stmt.order_by(Delivery.id).limit(limit))).scalars())
