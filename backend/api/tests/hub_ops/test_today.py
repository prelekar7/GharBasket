"""hub_ops.api.packing_progress and inbound_progress: how a hub's packing and buying are going
(M12 §3.3's today board)."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.models import (
    PackingLine,
    PackingTask,
    ProcurementPlan,
    PurchaseOrder,
    Vendor,
)
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)
SALT = uuid.UUID(int=1)
MILK = uuid.UUID(int=2)


def _task(status: str, bags: int = 1, **values: object) -> PackingTask:
    defaults: dict[str, object] = {
        "hub_id": SEED_HUB_ID,
        "order_id": uuid.uuid4(),
        "order_kind": "box",
        "delivery_date": TUESDAY,
        "slot_id": uuid.uuid4(),
        "society_id": uuid.uuid4(),
        "seq": 1,
        "status": status,
        "bags": bags,
    }
    return PackingTask(**(defaults | values))


def _line(task: PackingTask, product: uuid.UUID, short: int = 0, **values: object) -> PackingLine:
    defaults: dict[str, object] = {
        "task_id": task.id,
        "product_id": product,
        "step_qty": 1,
        "step_unit": "kg",
        "qty": 2,
        "picked_qty": 2 - short,
        "short_qty": short,
    }
    return PackingLine(**(defaults | values))


async def test_packing_progress_counts_bags_and_shortages_by_product(
    db_session: AsyncSession,
) -> None:
    await seed_hubs(db_session)
    first, second, third = _task("packed", 2), _task("picking"), _task("queued", 4)
    other_day = _task("queued", delivery_date=TUESDAY + timedelta(days=1))
    first.packed_at = datetime(2026, 11, 3, 5, 40, tzinfo=UTC)
    db_session.add_all([first, second, third, other_day])
    await db_session.flush()
    db_session.add_all(
        [
            _line(first, SALT, short=1),
            _line(first, MILK, short=0),
            _line(second, SALT, short=2),
            _line(third, MILK, short=1),
            _line(third, MILK, short=1, carried=True),  # the same bag counts once, and not carried
            _line(other_day, SALT, short=1),  # another day
        ]
    )
    await db_session.flush()

    progress = await hub_ops_api.packing_progress(db_session, hub_id=SEED_HUB_ID, on=TUESDAY)

    assert (progress.bags_total, progress.bags_packed, progress.bags_picking) == (7, 2, 1)
    assert progress.finished_at is None  # two bags are not packed
    # Most bags first; the carried line and the other day are not counted
    assert progress.shortages == (
        hub_ops_api.ShortProduct(MILK, bags=4),
        hub_ops_api.ShortProduct(SALT, bags=3),
    )

    empty = await hub_ops_api.packing_progress(db_session, hub_id=uuid.uuid4(), on=TUESDAY)
    assert empty == hub_ops_api.PackingProgress(0, 0, 0, None, ())


async def test_packing_is_finished_when_every_bag_is_packed(db_session: AsyncSession) -> None:
    await seed_hubs(db_session)
    early, late = _task("packed"), _task("packed", 2)
    early.packed_at = datetime(2026, 11, 3, 5, 10, tzinfo=UTC)
    late.packed_at = datetime(2026, 11, 3, 5, 55, tzinfo=UTC)
    db_session.add_all([early, late])
    await db_session.flush()

    progress = await hub_ops_api.packing_progress(db_session, hub_id=SEED_HUB_ID, on=TUESDAY)

    assert (progress.bags_total, progress.bags_packed) == (3, 3)
    assert progress.finished_at == late.packed_at
    assert progress.shortages == ()


async def test_inbound_progress_reads_the_plan_and_its_purchase_orders(
    db_session: AsyncSession,
) -> None:
    await seed_hubs(db_session)
    none = await hub_ops_api.inbound_progress(db_session, hub_id=SEED_HUB_ID, on=TUESDAY)
    assert none == hub_ops_api.InboundProgress(None, 0, 0, 0)

    plan = ProcurementPlan(
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        status="ordered",
        source="fallback",
        buffer_pct=10,
        built_at=datetime(2026, 11, 2, 15, 40, tzinfo=UTC),
    )
    vendors = [Vendor(hub_id=SEED_HUB_ID, name=f"Vendor {i}", kind="wholesaler") for i in range(5)]
    db_session.add_all([plan, *vendors])
    await db_session.flush()
    for vendor, status in zip(
        vendors, ["draft", "sent", "partly_received", "received", "cancelled"], strict=True
    ):
        db_session.add(
            PurchaseOrder(
                plan_id=plan.id,
                hub_id=SEED_HUB_ID,
                vendor_id=vendor.id,
                delivery_date=TUESDAY,
                status=status,
                plan_version=1,
                total_paise=0,
                unpriced_lines=0,
                share_text="PO draft",
            )
        )
    await db_session.flush()

    progress = await hub_ops_api.inbound_progress(db_session, hub_id=SEED_HUB_ID, on=TUESDAY)

    assert progress == hub_ops_api.InboundProgress(
        plan_status="ordered", pos_draft=1, pos_sent=3, pos_received=1
    )
