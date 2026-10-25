"""What M12's today board needs from M6 (M12 §3.3): how packing and buying are going for a hub
and a delivery date, as counts. Ids, timestamps and numbers only; no packer, flat or vendor."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops.models import (
    PackingLine,
    PackingTask,
    ProcurementPlan,
    PurchaseOrder,
)


@dataclass(frozen=True)
class ShortProduct:
    product_id: uuid.UUID  # as ordered
    bags: int  # bags with at least one step of it short


@dataclass(frozen=True)
class PackingProgress:
    bags_total: int
    bags_packed: int
    bags_picking: int  # started, not packed yet
    finished_at: datetime | None  # the last bag's `packed_at`, once every bag is packed
    shortages: tuple[ShortProduct, ...]  # most bags first, then by product id


@dataclass(frozen=True)
class InboundProgress:
    plan_status: str | None  # draft | ordered | closed; None before the plan is built
    pos_draft: int  # purchase orders not sent yet (cancelled ones left out)
    pos_sent: int  # sent, partly received or received
    pos_received: int  # received in full


async def packing_progress(
    session: AsyncSession, *, hub_id: uuid.UUID, on: date
) -> PackingProgress:
    """Bags of the hub's packing tasks for the IST delivery date `on`: all, packed and being
    picked, when the last one was packed, and the bags short of each product."""
    packed = PackingTask.status == "packed"
    total, packed_bags, picking, last, undone = (
        await session.execute(
            select(
                func.coalesce(func.sum(PackingTask.bags), 0),
                func.coalesce(func.sum(case((packed, PackingTask.bags))), 0),
                func.coalesce(
                    func.sum(case((PackingTask.status == "picking", PackingTask.bags))), 0
                ),
                func.max(PackingTask.packed_at),
                func.count().filter(PackingTask.status != "packed"),
            ).where(PackingTask.hub_id == hub_id, PackingTask.delivery_date == on)
        )
    ).one()
    short = (
        select(PackingLine.product_id, PackingTask.id, PackingTask.bags)
        .join(PackingTask, PackingTask.id == PackingLine.task_id)
        .where(
            PackingTask.hub_id == hub_id,
            PackingTask.delivery_date == on,
            PackingLine.short_qty > 0,
            PackingLine.carried.is_(False),
        )
        .distinct()
        .subquery()
    )
    rows = (
        await session.execute(
            select(short.c.product_id, func.sum(short.c.bags)).group_by(short.c.product_id)
        )
    ).all()
    return PackingProgress(
        bags_total=int(total),
        bags_packed=int(packed_bags),
        bags_picking=int(picking),
        finished_at=last if int(total) > 0 and int(undone) == 0 else None,
        shortages=tuple(
            ShortProduct(product_id, int(bags))
            for product_id, bags in sorted(rows, key=lambda r: (-int(r[1]), str(r[0])))
        ),
    )


async def inbound_progress(
    session: AsyncSession, *, hub_id: uuid.UUID, on: date
) -> InboundProgress:
    """The hub's procurement plan and purchase orders for the delivery date `on`."""
    plan_status = await session.scalar(
        select(ProcurementPlan.status).where(
            ProcurementPlan.hub_id == hub_id, ProcurementPlan.delivery_date == on
        )
    )
    counts = dict(
        (
            await session.execute(
                select(PurchaseOrder.status, func.count())
                .where(
                    PurchaseOrder.hub_id == hub_id,
                    PurchaseOrder.delivery_date == on,
                    PurchaseOrder.status != "cancelled",
                )
                .group_by(PurchaseOrder.status)
            )
        ).all()
    )
    draft = int(counts.get("draft", 0))
    return InboundProgress(
        plan_status=plan_status,
        pos_draft=draft,
        pos_sent=sum(int(n) for status, n in counts.items() if status != "draft"),
        pos_received=int(counts.get("received", 0)),
    )
