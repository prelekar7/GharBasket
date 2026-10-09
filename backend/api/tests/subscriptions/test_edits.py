"""Editing, approving (the hand-over to M4), skipping, the Budget Lock, auto-approve and the
pantry feedback (M3 §3.1, part 2)."""

import uuid
from datetime import UTC, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as orders_service
from gharbasket.modules.subscriptions.models import EditFeedback, PantryFeedback, WeeklyCycle
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import GENERATE_AT, MEENA, NOTICE_AT, NOW, PRIYA, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

CUTOFF = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)  # Mon 21:00 IST for the Tuesday box


async def generate(api: httpx2.AsyncClient) -> None:
    """The week as the customer first sees it: drafted at 18:00, shown at 18:30 IST."""
    for name, at in (("generate_weekly_plans", GENERATE_AT), ("send_box_ready_notices", NOTICE_AT)):
        answer = await api.post(f"/v1/internal/jobs/{name}", json={"scheduled_for": at})
        assert answer.status_code == 200, answer.text


async def events(db: AsyncSession, event_type: str) -> list[dict]:
    rows = await db.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type))
    return [row.payload for row in rows.scalars()]


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


@time_machine.travel(NOW, tick=False)
async def test_edit_approve_hand_over_and_skip(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    box = (await api.get("/v1/cycles/current", headers=priya)).json()
    cycle_id, lines = box["id"], box["lines"]
    patch = f"/v1/cycles/{cycle_id}/lines"
    assert box["version"] == 1 and len(lines) >= 4 and box["total_paise"] > 50_000
    # Something the hub sells that is not in the box yet, and a line with a substitute that is
    # neither in the box (it would merge into that line) nor the product added below.
    catalog = await catalog_api.plan_catalog(db_session, SEED_HUB_ID)
    on_box = {line["product_id"] for line in lines}
    extra = next(p for p in catalog.products.values() if p.price_paise and str(p.id) not in on_box)
    swap = None
    for line in lines:
        offered = (
            await api.get(f"/v1/products/{line['product_id']}/substitutes", headers=priya)
        ).json()["items"]
        fresh = [s for s in offered if s["product_id"] not in on_box | {str(extra.id)}]
        if fresh:
            swap = (line, fresh[0])
            break
    assert swap is not None, "no box line has a substitute outside the box"
    await db_session.commit()

    # Refusals leave the box alone.
    wrong_version = await api.patch(
        patch,
        json={"ops": [{"op": "remove", "product_id": lines[0]["product_id"]}]},
        headers={**priya, "If-Match": '"9"'},
    )
    unknown = await api.patch(
        patch,
        json={"ops": [{"op": "qty", "product_id": str(uuid.uuid4()), "qty": 2}]},
        headers={**priya, "If-Match": '"1"'},
    )
    not_offered = await api.patch(
        patch,
        json={
            "ops": [
                {
                    "op": "substitute",
                    "product_id": lines[0]["product_id"],
                    "substitute_id": lines[0]["product_id"],  # never offered for itself
                }
            ]
        },
        headers={**priya, "If-Match": '"1"'},
    )
    assert (wrong_version.status_code, wrong_version.json()["code"]) == (
        412,
        "subscriptions.version_conflict",
    )
    assert unknown.json()["code"] == "subscriptions.product_unknown"
    assert not_offered.json()["code"] == "subscriptions.substitute_unknown"

    # A ₹500 Budget Lock is less than this box: the edit is refused with the overshoot.
    locked = await api.put(
        "/v1/budget-lock",
        json={"month": "2026-10", "budget_paise": 50_000},
        headers={**priya, **key()},
    )
    assert locked.status_code == 200 and locked.json()["remaining_paise"] == 50_000
    over = await api.patch(
        patch,
        json={"ops": [{"op": "qty", "product_id": lines[0]["product_id"], "qty": 20}]},
        headers={**priya, "If-Match": '"1"'},
    )
    assert (over.status_code, over.json()["code"]) == (422, "subscriptions.budget_exceeded")
    assert over.json()["over_paise"] > 0
    off = await api.put(
        "/v1/budget-lock",
        json={"month": "2026-10", "budget_paise": None},
        headers={**priya, **key()},
    )
    assert off.status_code == 204
    assert (await api.get("/v1/budget-lock?month=2026-10", headers=priya)).status_code == 404
    bad_month = await api.put(
        "/v1/budget-lock",
        json={"month": "2027-03", "budget_paise": 100_000},
        headers={**priya, **key()},
    )
    assert bad_month.json()["code"] == "subscriptions.budget_invalid"

    # Four ops in one go: add, more of one, drop one, swap one.
    line_c, substitute = swap
    line_a, line_b = [line for line in lines if line is not line_c][:2]
    edited = await api.patch(
        patch,
        json={
            "ops": [
                {"op": "add", "product_id": str(extra.id), "qty": 2},
                {"op": "qty", "product_id": line_a["product_id"], "qty": line_a["qty"] + 1},
                {"op": "remove", "product_id": line_b["product_id"]},
                {
                    "op": "substitute",
                    "product_id": line_c["product_id"],
                    "substitute_id": substitute["product_id"],
                },
            ]
        },
        headers={**priya, "If-Match": '"1"'},
    )
    assert edited.status_code == 200, edited.text
    body = edited.json()
    by_product = {line["product_id"]: line for line in body["lines"]}
    assert body["version"] == 2 and edited.headers["etag"] == '"2"'
    added = by_product[str(extra.id)]
    assert (added["qty"], added["reason_code"], added["source"]) == (2, "added_by_you", "customer")
    assert added["unit_price_paise"] == extra.price_paise
    assert by_product[line_a["product_id"]]["qty"] == line_a["qty"] + 1
    assert line_b["product_id"] not in by_product
    swapped = by_product[substitute["product_id"]]
    assert swapped["reason_code"] == "swapped_by_you" and swapped["source"] == "customer"
    assert swapped["replaces"]["product_id"] == line_c["product_id"]
    assert swapped["replaces"]["slug"] == line_c["slug"] and swapped["qty"] == line_c["qty"]
    assert line_c["product_id"] not in by_product
    feedback = (
        await db_session.execute(select(EditFeedback).where(EditFeedback.cycle_id == cycle_id))
    ).scalars()
    feedback = feedback.all()
    assert sorted(row.action for row in feedback) == ["added", "qty_up", "removed", "swapped"]
    # Stamped from the service clock, not Postgres now(), so simulated time reaches them.
    assert {row.at for row in feedback} == {NOW}
    cycle_row = await db_session.get(WeeklyCycle, uuid.UUID(cycle_id))
    assert cycle_row is not None
    assert (cycle_row.generated_at, cycle_row.notice_sent_at) == (NOW, NOW)

    # Approval hands the box to M4 in the same transaction: the answer carries the order.
    approved = await api.post(
        f"/v1/cycles/{cycle_id}/approve", headers={**priya, **key(), "If-Match": '"2"'}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved" and approved.json()["version"] == 3
    order_id = uuid.UUID(approved.json()["order_id"])
    assert approved.json()["approved_at"] is not None
    order = await orders_api.get(db_session, order_id)
    order_lines = await orders_service.lines_of(db_session, order_id)
    assert (order.status, order.kind, order.cycle_id) == ("approved", "box", uuid.UUID(cycle_id))
    assert {line.product_id for line in order_lines} == {uuid.UUID(p) for p in by_product}
    assert order.total_paise == body["total_paise"]
    handed = await events(db_session, "subscriptions.box_approved")
    assert (
        len(handed) == 1
        and handed[0]["version"] == 3
        and len(handed[0]["lines"]) == len(by_product)
        and handed[0]["auto_approved"] is False  # Priya approved it herself: M11 stays quiet
    )
    again = await api.post(
        f"/v1/cycles/{cycle_id}/approve", headers={**priya, **key(), "If-Match": '"3"'}
    )
    assert again.json()["version"] == 3  # approving twice changes nothing

    # An edit after approval keeps the status and refreshes the order's box lines.
    after = await api.patch(
        patch,
        json={"ops": [{"op": "qty", "product_id": str(extra.id), "qty": 5}]},
        headers={**priya, "If-Match": '"3"'},
    )
    assert after.status_code == 200 and after.json()["status"] == "approved"
    assert after.json()["version"] == 4 and after.json()["order_id"] == str(order_id)
    db_session.expire_all()
    refreshed = {
        line.product_id: line for line in await orders_service.lines_of(db_session, order_id)
    }
    assert (
        refreshed[extra.id].qty == 5 and (await orders_api.get(db_session, order_id)).version == 2
    )
    assert len(await events(db_session, "subscriptions.box_approved")) == 2

    # A skip cancels the order; a skipped week cannot be edited.
    skipped = await api.post(f"/v1/cycles/{cycle_id}/skip", headers={**priya, **key()})
    assert skipped.status_code == 200 and skipped.json()["status"] == "skipped"
    db_session.expire_all()
    assert (await orders_api.get(db_session, order_id)).status == "cancelled"
    assert len(await events(db_session, "subscriptions.box_skipped")) == 1
    assert (await api.get("/v1/cycles/current", headers=priya)).json()["budget_lock"] is None
    await db_session.commit()
    stuck = await api.patch(
        patch,
        json={"ops": [{"op": "qty", "product_id": str(extra.id), "qty": 1}]},
        headers={**priya, "If-Match": '"5"'},
    )
    assert (stuck.status_code, stuck.json()["code"]) == (409, "subscriptions.not_editable")


@time_machine.travel(NOW, tick=False)
async def test_auto_approve_the_cutoff_and_the_feedback_window(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    await subscribe(api, priya)
    await subscribe(api, meena)
    await api.patch("/v1/subscriptions/current", json={"auto_approve": True}, headers=priya)
    await generate(api)
    priya_cycle = (await api.get("/v1/cycles/current", headers=priya)).json()
    meena_cycle = (await api.get("/v1/cycles/current", headers=meena)).json()
    product = priya_cycle["lines"][0]["product_id"]
    another = priya_cycle["lines"][1]["product_id"]

    too_early = await api.post(
        f"/v1/cycles/{priya_cycle['id']}/feedback",
        json={"items": [{"product_id": product, "signal": "ran_out"}]},
        headers={**priya, **key()},
    )
    assert too_early.json()["code"] == "subscriptions.feedback_window_closed"
    await db_session.commit()

    # T-1 20:55 IST: Priya asked for auto-approve (notice went out 24 h before); Meena did not.
    with time_machine.travel(datetime(2026, 10, 12, 15, 25, tzinfo=UTC), tick=False):
        summary = (
            await api.post(
                "/v1/internal/jobs/apply_auto_approve",
                json={"scheduled_for": "2026-10-12T15:25:00Z"},
            )
        ).json()["summary"]
    assert summary == {"approved": 1, "failed": 0, "delivery_date": "2026-10-13"}
    cycles = {c.id: c for c in (await db_session.execute(select(WeeklyCycle))).scalars().all()}
    approved = cycles[uuid.UUID(priya_cycle["id"])]
    assert (approved.status, approved.auto_approved) == ("approved", True)
    assert approved.order_id is not None
    assert cycles[uuid.UUID(meena_cycle["id"])].status == "awaiting_approval"
    (handed,) = await events(db_session, "subscriptions.box_approved")
    assert handed["cycle_id"] == priya_cycle["id"] and handed["auto_approved"] is True
    await db_session.commit()

    # After the cut-off nothing on the week can change.
    with time_machine.travel(CUTOFF, tick=False):
        meena, _ = await as_user(api, google, MEENA)
        late_approve = await api.post(
            f"/v1/cycles/{meena_cycle['id']}/approve",
            headers={**meena, **key(), "If-Match": '"1"'},
        )
        late_skip = await api.post(
            f"/v1/cycles/{meena_cycle['id']}/skip", headers={**meena, **key()}
        )
        late_edit = await api.patch(
            f"/v1/cycles/{meena_cycle['id']}/lines",
            json={"ops": [{"op": "remove", "product_id": meena_cycle["lines"][0]["product_id"]}]},
            headers={**meena, "If-Match": '"1"'},
        )
    for answer in (late_approve, late_skip, late_edit):
        assert (answer.status_code, answer.json()["code"]) == (409, "subscriptions.cutoff_passed")

    # From delivery day for 14 days: one row per product, the latest tap wins.
    with time_machine.travel(datetime(2026, 10, 14, 8, 0, tzinfo=UTC), tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        first = await api.post(
            f"/v1/cycles/{priya_cycle['id']}/feedback",
            json={
                "items": [
                    {"product_id": product, "signal": "ran_out"},
                    {"product_id": another, "signal": "wasted"},
                ]
            },
            headers={**priya, **key()},
        )
        second = await api.post(
            f"/v1/cycles/{priya_cycle['id']}/feedback",
            json={"items": [{"product_id": product, "signal": "just_right"}]},
            headers={**priya, **key()},
        )
        assert (first.status_code, first.json()) == (201, {"recorded": 2})
        assert (second.status_code, second.json()) == (201, {"recorded": 1})
        rows = (await db_session.execute(select(PantryFeedback))).scalars().all()
        assert {(str(r.product_id), r.signal) for r in rows} == {
            (product, "just_right"),
            (another, "wasted"),
        }
        assert {r.at for r in rows} == {datetime(2026, 10, 14, 8, 0, tzinfo=UTC)}
        await db_session.commit()
        stranger = await api.post(
            f"/v1/cycles/{priya_cycle['id']}/feedback",
            json={"items": [{"product_id": str(uuid.uuid4()), "signal": "wasted"}]},
            headers={**priya, **key()},
        )
        assert stranger.json()["code"] == "subscriptions.product_unknown"
    with time_machine.travel(datetime(2026, 10, 28, 8, 0, tzinfo=UTC), tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        closed = await api.post(
            f"/v1/cycles/{priya_cycle['id']}/feedback",
            json={"items": [{"product_id": product, "signal": "wasted"}]},
            headers={**priya, **key()},
        )
        assert closed.json()["code"] == "subscriptions.feedback_window_closed"
