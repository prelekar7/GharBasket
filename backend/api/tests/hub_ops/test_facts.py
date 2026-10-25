"""hub_ops.api.hub_day_facts and hub_data_sources: M12's hub-day rows (M12 §3.1), packed grams and
the waste logs' grams by IST day and cascade (M6 §3.5)."""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.models import PackingLine, PackingTask, WasteLog
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)
IST = ZoneInfo("Asia/Kolkata")


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


async def test_hub_day_facts_weigh_the_waste_logs_by_ist_day_and_cascade(
    db_session: AsyncSession,
) -> None:
    await seed_hubs(db_session)
    db_session.add(_task(1, "packed"))

    def log(at: datetime, cascade_to: str, kg: str | None) -> WasteLog:
        return WasteLog(
            hub_id=SEED_HUB_ID,
            product_id=uuid.uuid4(),
            qty=Decimal(1),
            unit="kg",
            kg=None if kg is None else Decimal(kg),
            reason="spoiled",
            cascade_to=cascade_to,
            at=at,
        )

    def ist(day: date, hour: int, minute: int = 0) -> datetime:
        return datetime.combine(day, time(hour, minute), IST)

    wednesday = TUESDAY + timedelta(days=1)
    db_session.add_all(
        [
            log(ist(TUESDAY, 0, 15), "bin", "0.250"),  # 18:45 UTC on Monday: still Tuesday in IST
            log(ist(TUESDAY, 18), "bin", "0.100"),
            log(ist(TUESDAY, 13), "bin", None),  # no weight: left out, as in M6 §3.5's summary
            log(ist(TUESDAY, 12, 30), "ngo", "1.500"),
            log(ist(TUESDAY, 12, 30), "kitchen", "0.401"),
            log(ist(TUESDAY - timedelta(days=1), 23, 50), "bin", "9.000"),  # Monday, outside
            log(ist(wednesday, 9), "bin", "2.000"),  # a day with waste and no packing
        ]
    )
    await db_session.flush()

    facts = await hub_ops_api.hub_day_facts(db_session, date_from=TUESDAY, date_to=wednesday)

    assert [
        (f.on, f.packed_g, f.waste_bin_g, f.waste_ngo_g, f.waste_kitchen_g, f.waste_imperfect_g)
        for f in facts
    ] == [(TUESDAY, 0, 350, 1_500, 401, 0), (wednesday, 0, 2_000, 0, 0, 0)]
    assert {(f.hub_id, f.data_source, f.surplus_offered_g, f.surplus_taken_g) for f in facts} == {
        (SEED_HUB_ID, "synthetic", 0, 0)
    }
    only_monday = await hub_ops_api.hub_day_facts(
        db_session, date_from=TUESDAY - timedelta(days=1), date_to=TUESDAY - timedelta(days=1)
    )
    assert [(f.on, f.waste_bin_g) for f in only_monday] == [(TUESDAY - timedelta(days=1), 9_000)]
    assert (
        await hub_ops_api.hub_day_facts(
            db_session, date_from=TUESDAY, date_to=wednesday, hub_ids=[uuid.uuid4()]
        )
        == []
    )
