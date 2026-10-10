"""Un-skipping a week before its cut-off (M3 §3.3): back to approval with its lines, and a new
order when it is approved again (M4 keeps cycle_id unique only among live orders)."""

import uuid

import pytest
import time_machine
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions.models import WeeklyCycle

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import MEENA, NOW, PRIYA, as_user, subscribe
from .test_edits import events, generate, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]


def lines_of(week: dict) -> list[tuple[str, int, int]]:
    return sorted((li["product_id"], li["qty"], li["unit_price_paise"]) for li in week["lines"])


@time_machine.travel(NOW, tick=False)
async def test_unskip_brings_the_week_back_and_approval_makes_a_new_order(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    await subscribe(api, priya)
    await subscribe(api, meena)
    await generate(api)
    week = (await api.get("/v1/cycles/current", headers=priya)).json()
    cycle_id = week["id"]
    approved = await api.post(
        f"/v1/cycles/{cycle_id}/approve", headers={**priya, **key(), "If-Match": '"1"'}
    )
    first_order = uuid.UUID(approved.json()["order_id"])
    skipped = await api.post(f"/v1/cycles/{cycle_id}/skip", headers={**priya, **key()})
    assert (skipped.json()["status"], skipped.json()["version"]) == ("skipped", 3)
    await db_session.commit()

    unskip = f"/v1/cycles/{cycle_id}/unskip"
    no_tag = await api.post(unskip, headers={**priya, **key()})
    stale = await api.post(unskip, headers={**priya, **key(), "If-Match": '"2"'})
    stranger = await api.post(unskip, headers={**meena, **key(), "If-Match": '"3"'})
    assert (no_tag.status_code, no_tag.json()["code"]) == (428, "request.precondition_required")
    assert (stale.status_code, stale.json()["code"]) == (412, "subscriptions.version_conflict")
    assert (stranger.status_code, stranger.json()["code"]) == (404, "subscriptions.no_cycle")

    same = key()
    back = await api.post(unskip, headers={**priya, **same, "If-Match": '"3"'})
    replay = await api.post(unskip, headers={**priya, **same, "If-Match": '"3"'})
    assert back.status_code == 200, back.text
    body = back.json()
    assert (body["status"], body["version"], body["order_id"], body["approved_at"]) == (
        "awaiting_approval",
        4,
        None,
        None,
    )
    assert back.headers["ETag"] == '"4"' and lines_of(body) == lines_of(week)
    assert replay.headers["Idempotent-Replayed"] == "true" and replay.json() == body
    assert await events(db_session, "subscriptions.box_unskipped") == [
        {"cycle_id": cycle_id, "household_id": week["household_id"], "delivery_date": "2026-10-13"}
    ]
    await db_session.commit()
    twice = await api.post(unskip, headers={**priya, **key(), "If-Match": '"4"'})
    assert (twice.status_code, twice.json()["code"]) == (409, "subscriptions.not_skipped")

    # Approved again: a new order; the skipped week's order stays cancelled.
    again = await api.post(
        f"/v1/cycles/{cycle_id}/approve", headers={**priya, **key(), "If-Match": '"4"'}
    )
    assert again.status_code == 200, again.text
    second_order = uuid.UUID(again.json()["order_id"])
    assert second_order != first_order and again.json()["status"] == "approved"
    assert (await orders_api.get(db_session, first_order)).status == "cancelled"
    new = await orders_api.get(db_session, second_order)
    assert (new.status, new.cycle_id, new.total_paise) == (
        "approved",
        uuid.UUID(cycle_id),
        week["total_paise"],
    )


@time_machine.travel(NOW, tick=False)
async def test_unskip_waits_for_a_resume_and_returns_a_draft_without_its_notice(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    meena, _ = await as_user(api, google, MEENA)
    await subscribe(api, meena)
    await generate(api)
    cycle_id = (await api.get("/v1/cycles/current", headers=meena)).json()["id"]
    skipped = await api.post(f"/v1/cycles/{cycle_id}/skip", headers={**meena, **key()})
    assert skipped.status_code == 200
    await api.patch("/v1/subscriptions/current", json={"paused_until": "2026-10-20"}, headers=meena)
    await db_session.execute(
        update(WeeklyCycle).where(WeeklyCycle.id == uuid.UUID(cycle_id)).values(notice_sent_at=None)
    )
    await db_session.commit()

    unskip = f"/v1/cycles/{cycle_id}/unskip"
    paused = await api.post(unskip, headers={**meena, **key(), "If-Match": '"2"'})
    assert (paused.status_code, paused.json()["code"]) == (409, "subscriptions.paused")

    await api.patch("/v1/subscriptions/current", json={"paused_until": None}, headers=meena)
    back = await api.post(unskip, headers={**meena, **key(), "If-Match": '"2"'})
    assert (back.status_code, back.json()["status"]) == (200, "draft")
