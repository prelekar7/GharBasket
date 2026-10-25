"""partners.api.partner_facts and assignment_run_facts: M12's partner rows (M12 §3.1)."""

import dataclasses
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners.models import AssignmentRun, Partner
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)


async def test_partner_facts_carry_no_personal_field(db_session: AsyncSession) -> None:
    hub = uuid.uuid4()
    kitchen = Partner(
        user_id=uuid.uuid4(),
        kind="kitchen",
        hub_id=hub,
        display_name="Asha's kitchen",
        verified_at=datetime.now(UTC),
        data_source="synthetic",
    )
    rider = Partner(
        user_id=uuid.uuid4(),
        kind="rider",
        hub_id=hub,
        display_name="Arjun",
        verified_at=datetime.now(UTC),
        status="paused",
    )
    db_session.add_all([kitchen, rider])
    await db_session.flush()

    facts = await partners_api.partner_facts(db_session, ids=[kitchen.id, rider.id])

    assert sorted((f.kind, f.status, f.data_source) for f in facts) == [
        ("kitchen", "active", "synthetic"),
        ("rider", "paused", "real"),
    ]
    assert {field.name for field in dataclasses.fields(partners_api.PartnerFact)} == {
        "partner_id",
        "hub_id",
        "kind",
        "status",
        "data_source",
    }
    assert {f.partner_id for f in await partners_api.partner_facts(db_session)} >= {
        kitchen.id,
        rider.id,
    }


async def test_assignment_run_facts_scale_the_fairness_numbers(db_session: AsyncSession) -> None:
    await seed_hubs(db_session)

    def run(on: date, summary: dict[str, object]) -> AssignmentRun:
        return AssignmentRun(
            hub_id=SEED_HUB_ID,
            on_date=on,
            meal="lunch",
            round=1,
            source="fallback",
            model="greedy",
            status="fallback",
            portions=12,
            unassigned=[],
            summary=summary,
            seconds=Decimal("0.10"),
        )

    solved = run(TUESDAY, {"gini": 0.1234, "km_per_portion": 1.25})
    empty = run(TUESDAY, {})
    db_session.add_all([solved, empty, run(date(2026, 11, 10), {"gini": 0.5})])
    await db_session.flush()

    facts = await partners_api.assignment_run_facts(
        db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[SEED_HUB_ID]
    )

    by_id = {f.run_id: f for f in facts}
    assert set(by_id) == {solved.id, empty.id}
    assert (by_id[solved.id].gini_bp, by_id[solved.id].km_per_portion_m) == (1_234, 1_250)
    assert (by_id[empty.id].gini_bp, by_id[empty.id].km_per_portion_m) == (None, None)
    assert {(f.portions, f.meal, f.data_source) for f in facts} == {(12, "lunch", "synthetic")}
