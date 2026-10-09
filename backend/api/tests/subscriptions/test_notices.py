"""The time-of-day notices around a week's box (M3 §6): a late box-ready notice, the cut-off
reminder, and auto-approve, which only counts a notice sent 24 h before the cut-off."""

import uuid
from datetime import UTC, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.subscriptions.models import WeeklyCycle

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import GENERATE_AT, MEENA, NOW, PRIYA, as_user, subscribe
from .test_edits import events, generate, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

SUNITA = "+919800001002"  # Green Valley Heights, Tuesday delivery too
RAMESH = "+919800001003"  # Krishna Kunj, Tuesday delivery too
LATE = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)  # Mon 18:30 IST, 2.5 h before the cut-off
REMIND_AT = "2026-10-12T13:30:00Z"  # Mon 19:00 IST, for Tuesday
AUTO_AT = "2026-10-12T15:25:00Z"  # Mon 20:55 IST


async def run(api: httpx2.AsyncClient, name: str, at: str) -> dict:
    answer = await api.post(f"/v1/internal/jobs/{name}", json={"scheduled_for": at})
    assert answer.status_code == 200, answer.text
    return answer.json()["summary"]


@time_machine.travel(NOW, tick=False)
async def test_the_reminder_goes_once_to_boxes_nothing_else_will_approve(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)  # auto-approve, told on time
    meena, _ = await as_user(api, google, MEENA)  # approves by hand, or not at all
    ramesh, _ = await as_user(api, google, RAMESH)  # approves by hand before the reminder
    for auth in (priya, meena, ramesh):
        await subscribe(api, auth)
    await api.patch("/v1/subscriptions/current", json={"auto_approve": True}, headers=priya)
    await generate(api)
    ramesh_box = (await api.get("/v1/cycles/current", headers=ramesh)).json()
    by_hand = await api.post(
        f"/v1/cycles/{ramesh_box['id']}/approve", headers={**ramesh, **key(), "If-Match": '"1"'}
    )
    assert by_hand.status_code == 200, by_hand.text
    await db_session.commit()

    # Sunita joins on Monday evening, auto-approve on: the generation run for Tuesday is redone
    # late and her draft is caught by Monday's notice run, too late for auto-approve to count.
    with time_machine.travel(LATE, tick=False):
        sunita, _ = await as_user(api, google, SUNITA)
        await subscribe(api, sunita)
        await api.patch("/v1/subscriptions/current", json={"auto_approve": True}, headers=sunita)
        assert (await run(api, "generate_weekly_plans", GENERATE_AT))["created"] == 1
        late = await run(api, "send_box_ready_notices", LATE.isoformat())
        assert late == {"sent": 1, "delivery_date": "2026-10-15"}
        sunita_box = (await api.get("/v1/cycles/current", headers=sunita)).json()
        assert sunita_box["status"] == "awaiting_approval" and sunita_box["version"] == 1
    await db_session.commit()

    with time_machine.travel(datetime.fromisoformat(REMIND_AT), tick=False):
        first = await run(api, "remind_unapproved", REMIND_AT)
        second = await run(api, "remind_unapproved", REMIND_AT)
    assert first == {"reminded": 2, "delivery_date": "2026-10-13"}
    assert second == {"reminded": 0, "delivery_date": "2026-10-13"}
    cycles = {c.household_id: c for c in (await db_session.execute(select(WeeklyCycle))).scalars()}
    reminded = {str(c.id): c for c in cycles.values() if c.reminded_at is not None}
    meena_box = (await api.get("/v1/cycles/current", headers=meena)).json()
    assert set(reminded) == {meena_box["id"], sunita_box["id"]}
    payloads = {p["cycle_id"]: p for p in await events(db_session, "subscriptions.cutoff_reminder")}
    assert set(payloads) == set(reminded)
    assert payloads[meena_box["id"]] == {
        "cycle_id": meena_box["id"],
        "household_id": str(reminded[meena_box["id"]].household_id),
        "delivery_date": "2026-10-13",
        "cutoff_at": "2026-10-12T15:30:00+00:00",
        "total_paise": meena_box["total_paise"],
    }
    assert len(await events(db_session, "subscriptions.box_ready")) == 4
    await db_session.commit()

    # 20:55: only Priya's notice went out 24 h before the cut-off.
    with time_machine.travel(datetime.fromisoformat(AUTO_AT), tick=False):
        summary = await run(api, "apply_auto_approve", AUTO_AT)
    assert summary == {"approved": 1, "failed": 0, "delivery_date": "2026-10-13"}
    db_session.expire_all()
    statuses = {
        str(c.id): (c.status, c.auto_approved)
        for c in (await db_session.execute(select(WeeklyCycle))).scalars()
    }
    assert statuses[sunita_box["id"]] == ("awaiting_approval", False)
    assert statuses[meena_box["id"]] == ("awaiting_approval", False)
    approved = {
        p["cycle_id"]: p["auto_approved"]
        for p in await events(db_session, "subscriptions.box_approved")
    }
    assert approved[ramesh_box["id"]] is False
    (auto,) = [cycle_id for cycle_id, flag in approved.items() if flag]
    assert statuses[auto] == ("approved", True)
    assert uuid.UUID(auto) not in {uuid.UUID(c) for c in reminded}
