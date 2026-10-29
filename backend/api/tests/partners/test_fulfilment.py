"""Tiffin assignments follow M4's events (M8 §3.4, §5): the kitchen shows on the order once she
accepts, the rider's pickup and the delivery move the assignment's orders and complete it, M7 finds
the pickups through the facade, and a partner who asks to delete her account leaves."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import fulfilment, kitchens
from gharbasket.modules.partners.models import Assignment, AssignmentOrder, Partner
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.platform.outbox import publish, relay
from gharbasket.tools.seed import (
    SEED_HUB_ID,
)

from .. import seed_cache as cached
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, PRIYA, VIKRAM, as_user
from .test_cooking import CHECKLIST, ON_THE_DAY, accepted_offer, key
from .tiffins import EVENING, T, kitchen, tag, tiffin_order, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"


@pytest.fixture
async def tiffin_world(db_session: AsyncSession) -> None:
    await cached.seed(db_session)
    await cached.seed_societies(db_session)
    await cached.seed_households(db_session)
    await cached.seed_catalog(db_session)
    await cached.seed_hubs(db_session)
    await cached.seed_partners(db_session)
    await tag(db_session, "tomato", "veg")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [EVENING]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def deliver_events(db: AsyncSession) -> None:
    """What the relay does after a commit: hand every pending event to its consumers."""
    while (await relay(db)).sent:
        pass


async def statuses(db: AsyncSession, assignment_id: str) -> dict[uuid.UUID, str]:
    rows = await db.execute(
        select(AssignmentOrder.order_id, AssignmentOrder.status).where(
            AssignmentOrder.assignment_id == uuid.UUID(assignment_id)
        )
    )
    return dict(rows.all())


async def test_accepting_shows_the_kitchen_on_the_orders_and_a_cancellation_clears_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    assert (await orders_api.get(db_session, order)).kitchen_partner_id is None  # only offered

    lata_auth, _ = await as_user(api, google, LATA)
    mine = await api.get(f"/v1/assignments?date={T}", headers=lata_auth)
    (offer,) = mine.json()["items"]
    accepted = await api.post(
        f"/v1/assignments/{offer['id']}/accept", headers={**lata_auth, **key()}
    )
    assert accepted.status_code == 200, accepted.text
    assert (await orders_api.get(db_session, order)).kitchen_partner_id == lata

    suspended = await api.post(
        f"/v1/partners/{lata}/suspend",
        json={"reason_code": "hygiene", "note": "Failed inspection"},
        headers={**vikram, **key()},
    )
    assert suspended.status_code == 200, suspended.text
    assert (await orders_api.get(db_session, order)).kitchen_partner_id is None


async def test_a_manual_assignment_shows_the_kitchen_at_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=0)
    order = await tiffin_order(db_session, PRIYA, 2)

    made = await api.post(
        f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/manual",
        json={"date": T.isoformat(), "order_ids": [str(order)], "partner_id": str(lata)},
        headers={**vikram, **key()},
    )

    assert made.status_code == 200, made.text
    assert (await orders_api.get(db_session, order)).kitchen_partner_id == lata


async def test_pickup_delivery_and_failure_move_the_orders_and_complete_the_assignment(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=4)
    first = await tiffin_order(db_session, PRIYA, 2)
    second = await tiffin_order(db_session, MEENA, 1)
    lata, offer = await accepted_offer(api, google, vikram)
    clock[0] = ON_THE_DAY
    ready = await api.post(
        f"/v1/assignments/{offer['id']}/ready",
        json={"checklist": CHECKLIST},
        headers={**lata, **key()},
    )
    assert ready.status_code == 200, ready.text
    rider, captain = uuid.uuid4(), uuid.uuid4()

    for order in (first, second):
        await orders_api.transition(
            db_session, order, "dispatched", actor="rider", actor_user_id=rider
        )
    await db_session.commit()
    await deliver_events(db_session)
    assert set((await statuses(db_session, offer["id"])).values()) == {"picked_up"}
    mine = await api.get(f"/v1/assignments?date={T}", headers=lata)
    assert {o["status"] for o in mine.json()["items"][0]["orders"]} == {"picked_up"}

    await orders_api.transition(
        db_session, first, "delivered", actor="captain", actor_user_id=captain
    )
    await db_session.commit()
    await deliver_events(db_session)
    await deliver_events(db_session)  # a second delivery of the same events changes nothing
    assignment = await db_session.get(Assignment, uuid.UUID(offer["id"]), populate_existing=True)
    assert assignment is not None and assignment.status == "ready"  # one order is still out
    assert await events(db_session, "partners.assignment_completed") == []

    await orders_api.transition(
        db_session, second, "undelivered", actor="rider", actor_user_id=rider
    )
    await db_session.commit()
    await deliver_events(db_session)
    await db_session.refresh(assignment)
    assert assignment.status == "completed"
    assert await statuses(db_session, offer["id"]) == {first: "delivered", second: "failed"}
    assert await events(db_session, "partners.assignment_completed") == [
        {
            "assignment_id": offer["id"],
            "partner_id": str(lata_id(offer)),
            "hub_id": str(SEED_HUB_ID),
            "date": T.isoformat(),
            "meal": "lunch",
            "portions_delivered": 2,
            "orders_failed": 1,
        }
    ]


def lata_id(offer: dict[str, Any]) -> uuid.UUID:
    return uuid.UUID(offer["partner"]["id"])


async def test_boxes_and_orders_nobody_holds_leave_assignments_alone(
    db_session: AsyncSession,
) -> None:
    for event_type in (orders_api.DELIVERED, orders_api.DISPATCHED, orders_api.DELIVERY_FAILED):
        for kind in ("tiffin", "box"):
            event = OutboxEvent(
                id=uuid.uuid4(),
                event_type=event_type,
                payload={"order_id": str(uuid.uuid4()), "kind": kind},
            )
            await fulfilment.on_order_event(db_session, event)  # no row: nothing to update
    assert (await db_session.execute(select(AssignmentOrder))).first() is None


async def test_m7_finds_the_pickups_and_scans_a_kitchens_code(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    await kitchen(db_session, LATA, "Lata tai", lunch=4)
    first = await tiffin_order(db_session, PRIYA, 2)
    second = await tiffin_order(db_session, MEENA, 1)
    lata, offer = await accepted_offer(api, google, vikram)
    clock[0] = ON_THE_DAY
    await api.patch(
        "/v1/kitchens/me",
        json={"address": "B-1103, Sunrise Towers"},
        headers={**lata, "If-Match": '"1"'},
    )

    (cooking,) = await partners_api.tiffin_pickups(db_session, SEED_HUB_ID, T, "lunch")
    assert (cooking.display_name, cooking.status, cooking.address) == (
        "Lata tai",
        "accepted",
        "B-1103, Sunrise Towers",
    )
    assert cooking.ready_by is not None and cooking.ready_by.strftime("%H:%M") == "11:30"
    assert set(cooking.order_ids) == {first, second}
    assert await partners_api.tiffin_pickups(db_session, SEED_HUB_ID, T, "dinner") == []

    await api.post(
        f"/v1/assignments/{offer['id']}/ready",
        json={"order_ids": [str(first)], "checklist": CHECKLIST},
        headers={**lata, **key()},
    )
    qr = f"GB1A:{uuid.UUID(offer['id']).hex}"
    scanned = await partners_api.pickup(db_session, qr, SEED_HUB_ID)
    assert scanned.assignment_id == uuid.UUID(offer["id"]) and scanned.order_ids == (first,)
    for bad in (qr, "GB1A:nothex", "GB1:" + qr[5:], f"GB1A:{uuid.uuid4().hex}"):
        hub = SEED_HUB_ID if bad != qr else uuid.uuid4()  # a known code, but another hub's
        with pytest.raises(ProblemError) as refused:
            await partners_api.pickup(db_session, bad, hub)
        assert (refused.value.status, refused.value.code) == (404, "partners.pickup_invalid")


async def test_a_partner_who_asks_to_delete_her_account_leaves_and_her_offers_are_cancelled(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=3)
    order = await tiffin_order(db_session, PRIYA, 2)
    _, offer = await accepted_offer(api, google, vikram)
    owner = await user_id(db_session, LATA)

    await publish(
        db_session,
        identity_api.DELETION_REQUESTED,
        {"user_id": str(owner), "household_id": None, "purge_on": "2027-02-14"},
    )
    await db_session.commit()
    await deliver_events(db_session)
    await deliver_events(db_session)

    partner = await db_session.get(Partner, lata, populate_existing=True)
    assert partner is not None and partner.status == "left" and partner.version == 2
    assignment = await db_session.get(Assignment, uuid.UUID(offer["id"]), populate_existing=True)
    assert assignment is not None and assignment.status == "cancelled"
    assert (await orders_api.get(db_session, order)).kitchen_partner_id is None
    left = [
        p for p in await events(db_session, "partners.suspended") if p["partner_id"] == str(lata)
    ]
    assert left == [
        {
            "partner_id": str(lata),
            "user_id": str(owner),
            "kind": "kitchen",
            "hub_id": str(SEED_HUB_ID),
            "reason_code": "left",
        }
    ]
    audited = await db_session.execute(
        select(AuditEntry.action).where(AuditEntry.entity_id == str(lata))
    )
    assert "partners.left" in audited.scalars().all()
    assert datetime.now(UTC)  # the clock fixture only moves the kitchens module's time
