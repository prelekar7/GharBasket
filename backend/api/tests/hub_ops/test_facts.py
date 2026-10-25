"""hub_ops.api.hub_day_facts and hub_data_sources: M12's hub-day rows (M12 §3.1)."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.models import PackingLine, PackingTask
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)


def _task(seq: int, status: str, delivery: date = TUESDAY) -> PackingTask:
    return PackingTask(
        hub_id=SEED_HUB_ID,
        order_id=uuid.uuid4(),
        order_kind="box",
        delivery_date=delivery,
        slot_id=uuid.uuid4(),
        society_id=uuid.uuid4(),
        seq=seq,
        status=status,
    )


async def test_hub_day_facts_weigh_what_was_packed(db_session: AsyncSession) -> None:
    await seed_hubs(db_session)
    packed, picking, next_week = _task(1, "packed"), _task(2, "picking"), _task(3, "packed")
    next_week.delivery_date = TUESDAY + timedelta(days=7)
    db_session.add_all([packed, picking, next_week])
    await db_session.flush()

    def line(task: PackingTask, unit: str, step_qty: int, qty: int, picked: int) -> PackingLine:
        return PackingLine(
            task_id=task.id,
            product_id=uuid.uuid4(),
            step_qty=step_qty,
            step_unit=unit,
            qty=qty,
            picked_qty=picked,
            short_qty=qty - picked,
        )

    db_session.add_all(
        [
            line(packed, "g", 500, 2, 2),  # 1 kg
            line(packed, "kg", 1, 3, 2),  # 2 kg picked, 1 short
            line(packed, "l", 1, 1, 1),  # 1 l as 1 kg
            line(packed, "piece", 1, 6, 6),  # no unit_g: weight unknown, left out
            line(picking, "kg", 5, 1, 1),  # not packed yet
        ]
    )
    await db_session.flush()

    facts = await hub_ops_api.hub_day_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)

    assert facts == [
        hub_ops_api.HubDayFact(
            hub_id=SEED_HUB_ID,
            on=TUESDAY,
            packed_g=4_000,
            waste_ngo_g=0,
            waste_bin_g=0,
            waste_kitchen_g=0,
            waste_imperfect_g=0,
            cogs_paise=None,
            surplus_offered_g=0,
            surplus_taken_g=0,
            data_source="synthetic",
        )
    ]
    assert (
        await hub_ops_api.hub_day_facts(
            db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[uuid.uuid4()]
        )
        == []
    )
    unknown = uuid.uuid4()
    assert await hub_ops_api.hub_data_sources(db_session, [SEED_HUB_ID, unknown]) == {
        SEED_HUB_ID: "synthetic"
    }
    assert await hub_ops_api.hub_data_sources(db_session, []) == {}
