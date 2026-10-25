"""The 12:30 run and the surplus cascade (spec §3.5).

The run (`surplus_cascade` at T 12:30 for every active hub, `POST …/surplus/run` for one) draws
packing down (§3.4), closes the day's buying, moves on the open offers whose time is up and offers
the new surplus: lots that are grade C or will not last tomorrow. Surplus goes to kitchens, then an
NGO, then the bin; each step is an offer or a waste log, and only the bin is waste for the KPI.
In v1 the hub manager settles offers after a phone call; M8 and M9 later settle them through
`hub_ops.api.settle_surplus` with the same rules.

Stock leaves a lot once, when the cascade takes it: an `offer` move when it is offered, or a
`waste` move when it goes straight to the bin. What later happens to an offer (taken, declined,
moved on) changes no stock, only where the food went.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import events, procurement, receiving, service, stock, waste
from gharbasket.modules.hub_ops.models import (
    DEFAULT_TIMINGS,
    InventoryLot,
    ProcurementPlan,
    PurchaseOrder,
    SurplusOffer,
    WasteLog,
)
from gharbasket.modules.hub_ops.schemas import SurplusOfferOut
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish

IST = procurement.IST
RUN_AT = time(12, 30)  # the run, and when the offers it makes expire the next day
KITCHEN, NGO, BIN = "kitchen", "ngo", "bin"


@dataclass(frozen=True)
class Taker:
    """Who took an offer: a kitchen partner of the hub (M8), or an NGO by name (until M9 lists
    NGOs)."""

    kind: str  # kitchen | ngo
    partner_id: uuid.UUID | None = None
    name: str | None = None


def offer_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.offer_not_found", "No such surplus offer.")


def _taker_invalid(reason: str, detail: str) -> ProblemError:
    return ProblemError(422, "hub_ops.taker_invalid", detail, extra={"reason": reason})


def end_of_tomorrow(today: date) -> datetime:
    """The end of the IST day after `today`: a lot whose best_before is not after it cannot be
    packed tomorrow (the line of §3.4's usable stock)."""
    return datetime.combine(today + timedelta(days=2), time(), IST)


def expiry(now: datetime) -> datetime:
    """The next IST day's 12:30, when the run moves an unsettled offer on."""
    day = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(day, RUN_AT, IST).astimezone(UTC)


# --- moving on (step 5) --------------------------------------------------------------------------


@dataclass
class Moved:
    """What one step of the cascade wrote."""

    offers: list[SurplusOffer] = field(default_factory=list[SurplusOffer])
    logs: list[WasteLog] = field(default_factory=list[WasteLog])


async def _weights(session: AsyncSession, product_ids: set[uuid.UUID]) -> dict[uuid.UUID, Any]:
    return await catalog_api.stock_products(session, list(product_ids))


async def _log(
    session: AsyncSession,
    lot: InventoryLot,
    qty: Decimal,
    *,
    reason: str,
    cascade_to: str,
    now: datetime,
    offer: SurplusOffer | None,
    taker: Taker | None = None,
    note: str | None = None,
    by_user_id: uuid.UUID | None = None,
) -> WasteLog:
    """A cascade log; without an offer the stock still sits on the lot, so a `waste` move takes
    it. Publishes `hub_ops.waste_logged`."""
    product = (await _weights(session, {lot.product_id})).get(lot.product_id)
    log = WasteLog(
        hub_id=lot.hub_id,
        lot_id=lot.id,
        product_id=lot.product_id,
        qty=qty,
        unit=lot.unit,
        kg=waste.weigh(qty, lot.unit, None if product is None else product.unit_g),
        reason=reason,
        cascade_to=cascade_to,
        offer_id=None if offer is None else offer.id,
        taken_by_kind=None if taker is None else taker.kind,
        taken_by_partner_id=None if taker is None else taker.partner_id,
        taken_by_name=None if taker is None else taker.name,
        note=note,
        logged_by=by_user_id,
        at=now,
    )
    session.add(log)
    await session.flush()
    if offer is None:
        await receiving.move(
            session,
            hub_id=lot.hub_id,
            product_id=lot.product_id,
            unit=lot.unit,
            kind="waste",
            ref_id=log.id,
            qty=-qty,
            lot=lot,
            reason=reason,
            by_user_id=by_user_id,
        )
    await waste.published(session, log)
    return log


async def _offer(
    session: AsyncSession,
    lot: InventoryLot,
    qty: Decimal,
    offered_to: str,
    now: datetime,
    previous: SurplusOffer | None,
) -> SurplusOffer:
    """An open offer until the next day's run; a new one (no `previous`) takes the stock off the
    lot with an `offer` move. Publishes `hub_ops.surplus_offered`."""
    offer = SurplusOffer(
        hub_id=lot.hub_id,
        lot_id=lot.id,
        product_id=lot.product_id,
        qty=qty,
        unit=lot.unit,
        offered_to=offered_to,
        status="open",
        previous_offer_id=None if previous is None else previous.id,
        expires_at=expiry(now),
        created_at=now,
    )
    session.add(offer)
    await session.flush()
    if previous is None:
        await receiving.move(
            session,
            hub_id=lot.hub_id,
            product_id=lot.product_id,
            unit=lot.unit,
            kind="offer",
            ref_id=offer.id,
            qty=-qty,
            lot=lot,
        )
    await publish(
        session,
        events.SURPLUS_OFFERED,
        {
            "offer_id": str(offer.id),
            "hub_id": str(offer.hub_id),
            "lot_id": str(lot.id),
            "product_id": str(lot.product_id),
            "qty": float(qty),
            "unit": lot.unit,
            "grade": lot.grade,
            "best_before": lot.best_before.astimezone(UTC).isoformat(),
            "offered_to": offered_to,
            "expires_at": offer.expires_at.astimezone(UTC).isoformat(),
        },
    )
    return offer


async def move_on(
    session: AsyncSession,
    lot: InventoryLot,
    qty: Decimal,
    now: datetime,
    *,
    offer: SurplusOffer | None,
    note: str | None = None,
    by_user_id: uuid.UUID | None = None,
) -> Moved:
    """Step 5: a quantity of `lot` leaving `offer` (or the lot itself, as new surplus) goes on.
    Past the lot's best_before: the bin (`expired`). Otherwise new surplus is offered to a
    kitchen, a kitchen offer's rest to an NGO, and an NGO offer's rest goes to the bin
    (`returned` for a returned lot, else `surplus`)."""
    if lot.best_before <= now:
        log = await _log(
            session,
            lot,
            qty,
            reason="expired",
            cascade_to=BIN,
            now=now,
            offer=offer,
            note=note,
            by_user_id=by_user_id,
        )
        return Moved(logs=[log])
    if offer is None or offer.offered_to == KITCHEN:
        to = KITCHEN if offer is None else NGO
        return Moved(offers=[await _offer(session, lot, qty, to, now, offer)])
    log = await _log(
        session,
        lot,
        qty,
        reason="returned" if lot.source == "returned" else "surplus",
        cascade_to=BIN,
        now=now,
        offer=offer,
        note=note,
        by_user_id=by_user_id,
    )
    return Moved(logs=[log])


# --- the run -------------------------------------------------------------------------------------


async def _close_buying(
    session: AsyncSession, hub_id: uuid.UUID, today: date, now: datetime
) -> int:
    """Step 2: today's partly received POs are received, sent ones cancelled `not_delivered`,
    and today's plan closed. The plan is locked before its POs (the procurement lock order)."""
    plan = await session.scalar(
        select(ProcurementPlan)
        .where(ProcurementPlan.hub_id == hub_id, ProcurementPlan.delivery_date == today)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    pos = list(
        await session.scalars(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.hub_id == hub_id,
                PurchaseOrder.delivery_date == today,
                PurchaseOrder.status.in_(("sent", "partly_received")),
            )
            .order_by(PurchaseOrder.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for po in pos:
        if po.status == "partly_received":
            po.status = "received"
        else:
            po.status, po.cancel_reason = "cancelled", "not_delivered"
        po.closed_at = now
        po.version += 1
    if plan is not None and plan.status != "closed":
        plan.status = "closed"
    await session.flush()
    return len(pos)


async def run(session: AsyncSession, hub_id: uuid.UUID, now: datetime) -> dict[str, int]:
    """The 12:30 run for one hub at `now` (the job's `scheduled_for`): steps 1 to 5 of §3.5. The hub
    row is locked, so runs of a hub follow one another; then packing tasks, lots by id, the plan
    and its POs, the offers by id and the surplus lots by id. The day's buying closes only when
    `now` (IST) is at or after the hub's `surplus` time, so an earlier manual run reports 0 closed
    POs and leaves POs and the plan alone. A second run the same day finds nothing to do: drawn
    tasks are marked, closed POs and settled offers are not looked at again, and surplus lots are
    left empty."""
    hub = await service.get_hub(session, hub_id, for_update=True)
    local = now.astimezone(IST)
    today = local.date()
    drawn = await stock.draw_down(session, hub_id, today, now)
    # A manager's run at 03:00 must not cancel the sent POs before the 04:00 receiving.
    surplus_at = time.fromisoformat(hub.timings.get("surplus", DEFAULT_TIMINGS["surplus"]))
    closed = await _close_buying(session, hub_id, today, now) if local.time() >= surplus_at else 0
    done = Moved()
    expired = list(
        await session.scalars(
            select(SurplusOffer)
            .where(
                SurplusOffer.hub_id == hub_id,
                SurplusOffer.status == "open",
                SurplusOffer.expires_at <= now,
            )
            .order_by(SurplusOffer.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for offer in expired:
        lot = await stock.get_lot(session, offer.lot_id)
        offer.status, offer.settled_at = "expired", now
        moved = await move_on(session, lot, offer.qty, now, offer=offer)
        done.offers += moved.offers
        done.logs += moved.logs
    lots = await session.scalars(
        select(InventoryLot)
        .where(
            InventoryLot.hub_id == hub_id,
            InventoryLot.qty_on_hand > 0,
            or_(InventoryLot.grade == "C", InventoryLot.best_before <= end_of_tomorrow(today)),
        )
        .order_by(InventoryLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    for lot in list(lots):
        moved = await move_on(session, lot, lot.qty_on_hand, now, offer=None)
        done.offers += moved.offers
        done.logs += moved.logs
    await session.flush()
    return {
        "drawn_tasks": drawn,
        "closed_purchase_orders": closed,
        "offers": len(done.offers),
        "binned": sum(log.cascade_to == BIN for log in done.logs),
    }


# --- settling ------------------------------------------------------------------------------------


async def get_offer(
    session: AsyncSession, offer_id: uuid.UUID, *, for_update: bool = False
) -> SurplusOffer:
    stmt = select(SurplusOffer).where(SurplusOffer.id == offer_id)
    offer = await session.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if for_update else stmt
    )
    if offer is None:
        raise offer_not_found()
    return offer


async def _checked_taker(session: AsyncSession, offer: SurplusOffer, taker: Taker | None) -> Taker:
    if taker is None:
        raise _taker_invalid("missing", "Say who took it.")
    if taker.kind != offer.offered_to:
        raise _taker_invalid("kind_mismatch", f"This offer is for a {offer.offered_to}.")
    if taker.kind == NGO:
        if not taker.name:
            raise _taker_invalid("missing", "Name the NGO.")
        return Taker(NGO, None, taker.name[:80])
    partner = (
        None
        if taker.partner_id is None
        else await partners_api.get_partner(session, taker.partner_id)
    )
    if (
        partner is None
        or partner.kind != "kitchen"
        or partner.hub_id != offer.hub_id
        or partner.status != "active"
    ):
        raise _taker_invalid("not_a_kitchen", "Not an active kitchen partner of this hub.")
    return Taker(KITCHEN, partner.id, partner.display_name)


@dataclass
class Settled:
    offer: SurplusOffer
    next: SurplusOffer | None
    logs: list[WasteLog]


async def settle(
    session: AsyncSession,
    offer_id: uuid.UUID,
    outcome: str,
    *,
    qty: float | None = None,
    taker: Taker | None = None,
    note: str | None = None,
    actor_user_id: uuid.UUID | None = None,
    now: datetime,
) -> Settled:
    """Settle an open offer (row-locked): `taken` logs what was taken (cascade_to = offered_to)
    and moves any rest on; `declined` moves it all on (step 5) and takes neither a taker nor a
    quantity."""
    if outcome not in ("taken", "declined"):
        raise ValueError(f"outcome must be taken or declined, not {outcome!r}")
    offer = await get_offer(session, offer_id, for_update=True)
    if offer.status != "open":
        raise ProblemError(
            409,
            "hub_ops.offer_settled",
            "This offer is settled already.",
            extra={"current_status": offer.status},
        )
    lot = await stock.get_lot(session, offer.lot_id)
    moved = Moved()
    if outcome == "declined":
        if taker is not None:
            raise _taker_invalid("declined", "A declined offer has no taker.")
        if qty is not None:
            raise ProblemError(
                422,
                "hub_ops.qty_invalid",
                "A declined offer moves on whole; send no quantity.",
                extra={"reason": "declined"},
            )
        offer.status = "declined"
        moved = await move_on(
            session, lot, offer.qty, now, offer=offer, note=note, by_user_id=actor_user_id
        )
    else:
        taken = offer.qty if qty is None else procurement.check_qty(qty, offer.unit)
        if taken == 0 or taken > offer.qty:
            raise ProblemError(
                422,
                "hub_ops.qty_invalid",
                "Take more than 0 and no more than was offered.",
                extra={"qty": float(offer.qty)},
            )
        who = await _checked_taker(session, offer, taker)
        offer.status, offer.taken_qty = "taken", taken
        moved.logs.append(
            await _log(
                session,
                lot,
                taken,
                reason="returned" if lot.source == "returned" else "surplus",
                cascade_to=offer.offered_to,
                now=now,
                offer=offer,
                taker=who,
                note=note,
                by_user_id=actor_user_id,
            )
        )
        if taken < offer.qty:
            rest = await move_on(
                session,
                lot,
                offer.qty - taken,
                now,
                offer=offer,
                note=note,
                by_user_id=actor_user_id,
            )
            moved.offers += rest.offers
            moved.logs += rest.logs
    offer.settled_by, offer.settled_at = actor_user_id, now
    await session.flush()
    return Settled(offer, moved.offers[0] if moved.offers else None, moved.logs)


# --- reading -------------------------------------------------------------------------------------


async def list_offers(
    session: AsyncSession, hub_id: uuid.UUID, day: date, status: str | None
) -> list[SurplusOffer]:
    """The hub's offers made on the IST day `day`, oldest first."""
    start, end = receiving.day_bounds(day)
    stmt = select(SurplusOffer).where(
        SurplusOffer.hub_id == hub_id,
        SurplusOffer.created_at >= start,
        SurplusOffer.created_at < end,
    )
    if status is not None:
        stmt = stmt.where(SurplusOffer.status == status)
    rows = await session.scalars(stmt.order_by(SurplusOffer.created_at, SurplusOffer.id))
    return list(rows)


async def offers_out(
    session: AsyncSession, offers: list[SurplusOffer], lang: Language
) -> list[SurplusOfferOut]:
    if not offers:
        return []
    lots = {
        x.id: x
        for x in await session.scalars(
            select(InventoryLot).where(InventoryLot.id.in_({o.lot_id for o in offers}))
        )
    }
    products = await _weights(session, {o.product_id for o in offers})
    taken = {
        log.offer_id: log
        for log in await session.scalars(
            select(WasteLog).where(
                WasteLog.offer_id.in_([o.id for o in offers]), WasteLog.cascade_to != BIN
            )
        )
    }

    def out(offer: SurplusOffer) -> SurplusOfferOut:
        lot, product = lots[offer.lot_id], products[offer.product_id]
        log = taken.get(offer.id)
        return SurplusOfferOut(
            id=offer.id,
            hub_id=offer.hub_id,
            lot_id=offer.lot_id,
            product_id=offer.product_id,
            slug=product.slug,
            name=localised(product.names, lang),
            unit=offer.unit,  # pyright: ignore[reportArgumentType]
            grade=lot.grade,  # pyright: ignore[reportArgumentType]
            best_before=lot.best_before,
            qty=float(offer.qty),
            offered_to=offer.offered_to,  # pyright: ignore[reportArgumentType]
            status=offer.status,  # pyright: ignore[reportArgumentType]
            taken_qty=None if offer.taken_qty is None else float(offer.taken_qty),
            taken_by=None if log is None else waste.taker_of(log),
            previous_offer_id=offer.previous_offer_id,
            expires_at=offer.expires_at,
            created_at=offer.created_at,
            settled_at=offer.settled_at,
        )

    return [out(o) for o in offers]
