"""Cooking tiffins (M8 §3.4): start and ready with the checklist through M4, manual assignment,
the tiffin board, refunds of tiffins nobody takes, and suspension releasing a kitchen's offers."""

import uuid
from datetime import UTC, datetime, time
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import OrderEvent
from gharbasket.modules.partners import kitchens
from gharbasket.modules.partners.models import Assignment, AssignmentEvent
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    seed,
    seed_catalog,
    seed_households,
    seed_hubs,
    seed_partners,
    seed_societies,
)

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, PRIYA, SANJAY, VIKRAM, as_user
from .tiffins import EVENING, T, kitchen, tag, tiffin_order, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"
MANUAL = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/manual"
BOARD = f"/v1/hubs/{SEED_HUB_ID}/tiffin-board?date={T}"
ON_THE_DAY = datetime(2027, 1, 15, 4, 30, tzinfo=UTC)  # T 10:00 IST
LATE = datetime(2027, 1, 15, 6, 30, tzinfo=UTC)  # T 12:00 IST, after ready_by 11:30
CHECKLIST = dict.fromkeys(
    ("handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"), True
)


@pytest.fixture
async def tiffin_world(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await seed_partners(db_session)
    await tag(db_session, "tomato", "veg")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [EVENING]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def accepted_offer(
    api: httpx2.AsyncClient, google: FakeGoogle, vikram: dict[str, str]
) -> tuple[dict[str, str], dict[str, Any]]:
    """Run round 1 and accept Lata's offer."""
    assert (await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})).json()[
        "offers"
    ] == 1
    lata, _ = await as_user(api, google, LATA)
    (offer,) = (await api.get(f"/v1/assignments?date={T}", headers=lata)).json()["items"]
    answer = await api.post(f"/v1/assignments/{offer['id']}/accept", headers={**lata, **key()})
    assert answer.status_code == 200, answer.text
    return lata, answer.json()


async def test_start_and_ready_move_the_orders_through_m4_as_the_kitchen(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    first = await tiffin_order(db_session, PRIYA, 2)
    second = await tiffin_order(db_session, MEENA, 1)
    lata, offer = await accepted_offer(api, google, vikram)
    url = f"/v1/assignments/{offer['id']}"

    too_early = await api.post(f"{url}/start", json={}, headers={**lata, **key()})
    clock[0] = ON_THE_DAY
    started = await api.post(
        f"{url}/start", json={"client_at": "2027-01-15T04:29:00Z"}, headers={**lata, **key()}
    )
    unticked = await api.post(
        f"{url}/ready",
        json={"checklist": {**CHECKLIST, "sealed": False}},
        headers={**lata, **key()},
    )
    stranger = await api.post(
        f"{url}/ready",
        json={"order_ids": [str(uuid.uuid4())], "checklist": CHECKLIST},
        headers={**lata, **key()},
    )
    done = await api.post(f"{url}/ready", json={"checklist": CHECKLIST}, headers={**lata, **key()})
    again = await api.post(f"{url}/ready", json={"checklist": CHECKLIST}, headers={**lata, **key()})

    assert code(too_early) == (409, "partners.assignment_not_accepted")
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "preparing" and started.json()["pickup_qr"] is None
    assert code(unticked) == (422, "partners.checklist_incomplete")
    assert code(stranger) == (422, "partners.order_not_in_assignment")
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["status"], body["checklist"], body["ready_at"]) == (
        "ready",
        CHECKLIST,
        "2027-01-15T04:30:00Z",
    )
    assert body["pickup_qr"] == f"GB1A:{uuid.UUID(body['id']).hex}"
    assert {o["status"] for o in body["orders"]} == {"ready"}
    assert code(again) == (409, "partners.assignment_not_accepted")
    for order_id in (first, second):
        assert (await orders_api.get(db_session, order_id)).status == "packed"
    moves = await db_session.execute(
        select(OrderEvent.to_status, OrderEvent.actor, OrderEvent.actor_user_id).where(
            OrderEvent.order_id == first, OrderEvent.to_status.in_(("picking", "packed"))
        )
    )
    assert {(to, actor) for to, actor, _ in moves.all()} == {
        ("picking", "kitchen"),
        ("packed", "kitchen"),
    }
    started_event = await db_session.scalar(
        select(AssignmentEvent).where(AssignmentEvent.action == "started")
    )
    assert started_event is not None and started_event.client_at is not None


async def test_ready_without_start_moves_both_steps_and_late_is_recorded(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    first = await tiffin_order(db_session, PRIYA, 2)
    second = await tiffin_order(db_session, MEENA, 1)
    lata, offer = await accepted_offer(api, google, vikram)
    url = f"/v1/assignments/{offer['id']}/ready"
    clock[0] = LATE

    one = await api.post(
        url, json={"order_ids": [str(first)], "checklist": CHECKLIST}, headers={**lata, **key()}
    )
    rest = await api.post(url, json={"checklist": CHECKLIST}, headers={**lata, **key()})

    assert one.json()["status"] == "preparing"
    assert one.json()["pickup_qr"] is not None  # an order is ready at the kitchen
    assert (await orders_api.get(db_session, first)).status == "packed"
    statuses = {o["order_id"]: o["status"] for o in one.json()["orders"]}
    assert statuses == {str(first): "ready", str(second): "assigned"}
    assert rest.json()["status"] == "ready"
    assignment = await db_session.get(Assignment, uuid.UUID(offer["id"]))
    assert assignment is not None and assignment.late is True


async def test_an_order_the_hub_moved_refuses_the_kitchens_step_and_changes_nothing(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order_id = await tiffin_order(db_session, PRIYA, 2)
    lata, offer = await accepted_offer(api, google, vikram)
    await orders_api.transition(db_session, order_id, "picking", actor="hub")
    await orders_api.transition(db_session, order_id, "packed", actor="hub")
    await db_session.commit()
    clock[0] = ON_THE_DAY

    started = await api.post(
        f"/v1/assignments/{offer['id']}/start", json={}, headers={**lata, **key()}
    )

    assert code(started) == (409, "orders.illegal_transition")
    assignment = await db_session.get(Assignment, uuid.UUID(offer["id"]))
    assert assignment is not None
    await db_session.refresh(assignment)
    assert assignment.status == "accepted"


async def test_manual_assignment_may_exceed_capacity_until_two_hours_before_ready_by(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, vikram_id = await as_user(api, google, VIKRAM)
    sanjay, _ = await as_user(api, google, SANJAY)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=1)
    first = await tiffin_order(db_session, PRIYA, 2)
    second = await tiffin_order(db_session, MEENA, 2)
    third = await tiffin_order(db_session, PRIYA, 1)
    dinner = await tiffin_order(db_session, MEENA, 1, start=time(19, 30))
    body = {"date": T.isoformat(), "partner_id": str(lata), "note": "Agreed on the phone"}

    mixed = await api.post(
        MANUAL, json={**body, "order_ids": [str(first), str(dinner)]}, headers={**vikram, **key()}
    )
    unknown = await api.post(
        MANUAL,
        json={**body, "partner_id": str(uuid.uuid4()), "order_ids": [str(first)]},
        headers={**vikram, **key()},
    )
    franchise = await api.post(
        MANUAL, json={**body, "order_ids": [str(first)]}, headers={**sanjay, **key()}
    )
    made = await api.post(
        MANUAL, json={**body, "order_ids": [str(first), str(second)]}, headers={**vikram, **key()}
    )
    twice = await api.post(
        MANUAL, json={**body, "order_ids": [str(first)]}, headers={**vikram, **key()}
    )
    clock[0] = datetime(2027, 1, 15, 4, 0, tzinfo=UTC)  # T 09:30 IST: ready_by 11:30 minus 2 h
    closed = await api.post(
        MANUAL, json={**body, "order_ids": [str(third)]}, headers={**vikram, **key()}
    )
    dinner_open = await api.post(
        MANUAL, json={**body, "order_ids": [str(dinner)]}, headers={**vikram, **key()}
    )

    assert code(mixed) == (409, "partners.order_not_assignable")
    assert mixed.json()["order_ids"] == [str(dinner)]
    assert code(unknown) == (404, "partners.kitchen_not_found")
    assert code(franchise) == (403, "auth.forbidden")
    assert made.status_code == 200, made.text
    assert (made.json()["status"], made.json()["source"], made.json()["portions"]) == (
        "accepted",
        "manual",
        4,
    )
    assert made.json()["over_capacity"] is True
    assert code(twice) == (409, "partners.order_not_assignable")
    assert code(closed) == (409, "partners.assignment_closed")
    assert dinner_open.status_code == 200 and dinner_open.json()["meal"] == "dinner"
    assert dinner_open.json()["over_capacity"] is True  # she cooks no dinners by default
    audit = (
        (
            await db_session.execute(
                select(AuditEntry).where(AuditEntry.action == "partners.manual_assignment")
            )
        )
        .scalars()
        .all()
    )
    assert audit[0].actor_user_id == vikram_id and audit[0].after is not None
    assert (
        audit[0].after["over_capacity"] is True and audit[0].after["note"] == "Agreed on the phone"
    )


async def test_the_board_counts_each_meal_and_lists_the_runs(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    sanjay, _ = await as_user(api, google, SANJAY)
    priya_auth, _ = await as_user(api, google, PRIYA)
    await kitchen(db_session, LATA, "Lata tai", lunch=2)
    await tiffin_order(db_session, PRIYA, 2)
    left_over = await tiffin_order(db_session, MEENA, 2)
    await accepted_offer(api, google, vikram)

    board = await api.get(BOARD, headers=sanjay)
    customer = await api.get(BOARD, headers=priya_auth)

    assert board.status_code == 200, board.text
    (lunch,) = board.json()["meals"]
    assert {k: lunch[k] for k in ("orders", "portions", "assigned", "accepted", "ready")} == {
        "orders": 2,
        "portions": 4,
        "assigned": 1,
        "accepted": 1,
        "ready": 0,
    }
    assert lunch["unassigned"] == [
        {
            "order_id": str(left_over),
            "society": {"id": lunch["unassigned"][0]["society"]["id"], "name": "Sunrise Towers"},
            "portions": 2,
            "reason": "no_room",
        }
    ]
    assert [a["status"] for a in board.json()["assignments"]] == ["accepted"]
    (run,) = board.json()["runs"]
    assert (run["round"], run["source"], run["model"]) == (1, "fallback", "greedy-m8")
    assert code(customer) == (403, "auth.forbidden")


async def test_locked_tiffins_nobody_took_are_refunded_once_manual_assignment_closes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=2)
    taken = await tiffin_order(db_session, PRIYA, 2)
    orphan = await tiffin_order(db_session, MEENA, 2)
    await accepted_offer(api, google, vikram)
    job = "/v1/internal/jobs/refund_unassigned_tiffins"

    early = await api.post(job, json={"scheduled_for": "2027-01-15T03:30:00Z"})  # 09:00 IST
    refunded = await api.post(job, json={"scheduled_for": "2027-01-15T04:00:00Z"})  # 09:30 IST
    again = await api.post(job, json={"scheduled_for": "2027-01-15T04:05:00Z"})

    summary = {"delivery_date": T.isoformat(), "refunded": 0, "refused": 0}
    assert early.json()["summary"] == summary
    assert refunded.json()["summary"] == {**summary, "refunded": 1}
    assert again.json()["summary"] == summary  # a refunded order is no longer locked
    # M4's locked → refunded move (ADR-0027): only the tiffin no assignment holds.
    states = {o: (await orders_api.get(db_session, o)).status for o in (taken, orphan)}
    assert states == {taken: "locked", orphan: "refunded"}
    meena = await user_id(db_session, MEENA)
    assert await billing_api.balance(db_session, meena) == (
        (await orders_api.get(db_session, orphan)).total_paise
    )


async def test_suspending_a_kitchen_cancels_her_open_assignments_and_releases_the_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    await tiffin_order(db_session, PRIYA, 2)
    _, offer = await accepted_offer(api, google, vikram)

    suspended = await api.post(
        f"/v1/partners/{lata}/suspend",
        json={"reason_code": "hygiene", "note": "Failed inspection"},
        headers={**vikram, **key()},
    )

    assert suspended.status_code == 200, suspended.text
    assignment = await db_session.get(Assignment, uuid.UUID(offer["id"]))
    assert assignment is not None
    await db_session.refresh(assignment)
    assert assignment.status == "cancelled"
    released = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "partners.assignment_released")
    )
    assert [p["reason"] for p in released.scalars()] == ["cancelled"]
