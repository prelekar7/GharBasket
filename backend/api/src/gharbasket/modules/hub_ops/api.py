"""Facade of M6 Hub Operations: the only module other modules may import (spec §3.1, §3.2).

M6 owns hub ids (ADR-0026 §5): other modules keep them as plain UUIDs and look hubs up here.
M12 pulls `hub_day_facts` for its analytics refresh (M12 §3.1); M7 and M8 tag their hub-level
facts with `hub_data_sources`. Its today board reads `packing_progress` and `inbound_progress`
(M12 §3.3).
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import kept, packing, service, surplus, waste
from gharbasket.modules.hub_ops.facts import HubDayFact, hub_day_facts
from gharbasket.modules.hub_ops.models import Hub, PackingEvent, PackingTask, SurplusOffer, WasteLog
from gharbasket.modules.hub_ops.surplus import Taker
from gharbasket.modules.hub_ops.today import (
    InboundProgress,
    PackingProgress,
    ShortProduct,
    inbound_progress,
    packing_progress,
)
from gharbasket.modules.orders import api as orders_api

__all__ = [
    "BagRef",
    "HubDayFact",
    "HubRef",
    "InboundProgress",
    "OfferRef",
    "PackingProgress",
    "SettleResult",
    "ShortProduct",
    "SocietyLoad",
    "Taker",
    "WasteDay",
    "WasteLogRef",
    "active_hub_ids",
    "anonymise_for_user",
    "bag_for_order",
    "get_hub",
    "hub_data_sources",
    "hub_day_facts",
    "inbound_progress",
    "open_surplus_offers",
    "packing_progress",
    "settle_surplus",
    "society_loads",
    "waste_summary",
]


@dataclass(frozen=True)
class HubRef:
    id: uuid.UUID
    slug: str
    name: str
    status: str  # active | paused
    lat: float
    lng: float
    timings: dict[str, str]  # receiving, packing, dispatch, surplus as IST "HH:MM"
    pre_live_box_fee_paise: int  # per box while the society is not live (M9 §3.6; M4 box_fee)


async def get_hub(session: AsyncSession, hub_id: uuid.UUID) -> HubRef | None:
    hub = await session.get(Hub, hub_id)
    if hub is None:
        return None
    return HubRef(
        id=hub.id,
        slug=hub.slug,
        name=hub.name,
        status=hub.status,
        lat=hub.lat,
        lng=hub.lng,
        timings=dict(hub.timings),
        pre_live_box_fee_paise=hub.pre_live_box_fee_paise,
    )


async def active_hub_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Hubs that are not paused, for jobs that run per hub."""
    return await service.active_hub_ids(session)


@dataclass(frozen=True)
class SocietyLoad:
    society_id: uuid.UUID
    bags: int
    packed_bags: int
    crates: int


async def society_loads(
    session: AsyncSession, hub_id: uuid.UUID, delivery_date: date
) -> list[SocietyLoad]:
    """Bags, packed bags and crates per society (M7's vehicle loads, the captain's handover
    counts); `crates` is the highest crate number packed so far."""
    loads = await packing.society_loads(session, hub_id, delivery_date)
    return [SocietyLoad(x.society_id, x.bags, x.packed_bags, x.crates) for x in loads]


@dataclass(frozen=True)
class BagRef:
    bag_qr: str  # "GB1:" + the order id without hyphens
    bags: int
    crate_no: int | None
    status: str  # queued | picking | packed


async def bag_for_order(session: AsyncSession, order_id: uuid.UUID) -> BagRef | None:
    """The bag of an order (M7's door scan); None until its packing task exists."""
    task = await packing.bag_for_order(session, order_id)
    if task is None:
        return None
    return BagRef(packing.bag_qr(task.order_id), task.bags, task.crate_no, task.status)


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5): packing tasks keep the bag and lose the flat copied from the
    address; tasks and events the user worked on as a packer lose the user id; the household's
    kept items still on the shelf are released into returned lots (§3.5) and lose their flat."""
    orders = (
        []
        if household_id is None
        else await orders_api.order_ids_of_household(session, household_id)
    )
    flats = await session.execute(
        update(PackingTask)
        .where(PackingTask.order_id.in_(orders), PackingTask.flat.is_not(None))
        .values(tower_id=None, flat=None)
    )
    packed = await session.execute(
        update(PackingTask).where(PackingTask.packer_user_id == user_id).values(packer_user_id=None)
    )
    acted = await session.execute(
        update(PackingEvent).where(PackingEvent.user_id == user_id).values(user_id=None)
    )
    released, scrubbed = (
        (0, 0) if household_id is None else await kept.release_household(session, household_id)
    )
    return {
        "packing_tasks": flats.rowcount + packed.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "packing_events": acted.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "kept_items": scrubbed + released,
    }


async def hub_data_sources(
    session: AsyncSession, hub_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Each known hub's data source (`real` or `synthetic`, ADR-0008); unknown ids are left out."""
    ids = set(hub_ids)
    if not ids:
        return {}
    rows = await session.execute(select(Hub.id, Hub.data_source).where(Hub.id.in_(ids)))
    return dict(rows.all())


@dataclass(frozen=True)
class WasteDay:
    date: date
    sold_kg: float  # packed that day
    wasted_kg: float  # cascade_to bin
    rescued_kg: dict[str, float]  # kitchen, ngo
    waste_per_100kg: float | None  # None when nothing was sold
    stockout_pct: float | None  # steps short or substituted / steps ordered
    unweighed_lines: int


async def waste_summary(
    session: AsyncSession, hub_id: uuid.UUID, date_from: date, date_to: date
) -> list[WasteDay]:
    """§3.5's daily rows from `date_from` to `date_to` (IST days, inclusive), zeros included, for
    M10's `monitor_drift` and M12's KPI tiles."""
    days = await waste.summary_days(session, hub_id, date_from, date_to)
    return [
        WasteDay(
            date=x.date,
            sold_kg=round(float(x.sold_kg), 3),
            wasted_kg=round(float(x.wasted_kg), 3),
            rescued_kg={kind: round(float(kg), 3) for kind, kg in x.rescued_kg.items()},
            waste_per_100kg=None if x.waste_per_100kg is None else float(x.waste_per_100kg),
            stockout_pct=None if x.stockout_pct is None else float(x.stockout_pct),
            unweighed_lines=x.unweighed_lines,
        )
        for x in days
    ]


@dataclass(frozen=True)
class OfferRef:
    id: uuid.UUID
    hub_id: uuid.UUID
    lot_id: uuid.UUID
    product_id: uuid.UUID
    qty: float
    unit: str
    offered_to: str  # kitchen | ngo
    status: str  # open | taken | declined | expired
    taken_qty: float | None
    previous_offer_id: uuid.UUID | None
    expires_at: datetime


@dataclass(frozen=True)
class WasteLogRef:
    id: uuid.UUID
    lot_id: uuid.UUID | None
    product_id: uuid.UUID
    qty: float
    unit: str
    kg: float | None
    reason: str
    cascade_to: str  # kitchen | ngo | bin
    offer_id: uuid.UUID | None


@dataclass(frozen=True)
class SettleResult:
    offer: OfferRef
    next: OfferRef | None  # the offer the rest moved on to
    waste_logs: list[WasteLogRef]  # written by this settle


def _offer_ref(offer: SurplusOffer) -> OfferRef:
    return OfferRef(
        id=offer.id,
        hub_id=offer.hub_id,
        lot_id=offer.lot_id,
        product_id=offer.product_id,
        qty=float(offer.qty),
        unit=offer.unit,
        offered_to=offer.offered_to,
        status=offer.status,
        taken_qty=None if offer.taken_qty is None else float(offer.taken_qty),
        previous_offer_id=offer.previous_offer_id,
        expires_at=offer.expires_at,
    )


def _log_ref(log: WasteLog) -> WasteLogRef:
    return WasteLogRef(
        id=log.id,
        lot_id=log.lot_id,
        product_id=log.product_id,
        qty=float(log.qty),
        unit=log.unit,
        kg=None if log.kg is None else float(log.kg),
        reason=log.reason,
        cascade_to=log.cascade_to,
        offer_id=log.offer_id,
    )


async def settle_surplus(
    session: AsyncSession,
    offer_id: uuid.UUID,
    outcome: str,
    *,
    qty: float | None = None,
    taken_by: Taker | None = None,
    actor_user_id: uuid.UUID | None = None,
    note: str | None = None,
) -> SettleResult:
    """Settle an open surplus offer (§3.5), for M8 (kitchens) and M9 (NGOs) when they consume
    `hub_ops.surplus_offered`; the console's settle endpoint runs the same code. `outcome` is
    `taken` (`qty` default all of it; `taken_by` a `Taker("kitchen", partner_id=…)` of an active
    kitchen partner of the hub, or `Taker("ngo", name=…)`) or `declined` (no taker, no `qty`);
    any other `outcome` is a `ValueError`. Raises 404 `hub_ops.offer_not_found`, 409
    `hub_ops.offer_settled`, 422 `hub_ops.taker_invalid` or `hub_ops.qty_invalid` (above the
    offer, or a `qty` on a decline). The caller commits."""
    done = await surplus.settle(
        session,
        offer_id,
        outcome,
        qty=qty,
        taker=taken_by,
        note=note,
        actor_user_id=actor_user_id,
        now=datetime.now(UTC),
    )
    return SettleResult(
        offer=_offer_ref(done.offer),
        next=None if done.next is None else _offer_ref(done.next),
        waste_logs=[_log_ref(x) for x in done.logs],
    )


async def open_surplus_offers(
    session: AsyncSession, *, hub_id: uuid.UUID
) -> tuple[int, datetime | None]:
    """How many surplus offers of the hub are open, and when the oldest was made (null when none),
    for M12's today board."""
    count, oldest = (
        await session.execute(
            select(func.count(SurplusOffer.id), func.min(SurplusOffer.created_at)).where(
                SurplusOffer.hub_id == hub_id, SurplusOffer.status == "open"
            )
        )
    ).one()
    return count, oldest
