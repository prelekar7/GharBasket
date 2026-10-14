"""Facade of M6 Hub Operations: the only module other modules may import (spec §3.1, §3.2).

M6 owns hub ids (ADR-0026 §5): other modules keep them as plain UUIDs and look hubs up here.
"""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import packing, service
from gharbasket.modules.hub_ops.models import Hub, PackingEvent, PackingTask
from gharbasket.modules.orders import api as orders_api


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
    address; tasks and events the user worked on as a packer lose the user id."""
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
    return {
        "packing_tasks": flats.rowcount + packed.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "packing_events": acted.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
    }
