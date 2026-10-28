"""What partners earn (M8 §3.6): the rate card's amount per event posted once through M5, the
week view that sums to the ledger, and the rolling earnings that steer the fair assignment."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import service as ledger
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as order_service
from gharbasket.modules.partners import earnings, kitchens
from gharbasket.modules.partners import service as partners_service
from gharbasket.modules.partners.models import Partner, RateCard
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import (
    SEED_HUB_ID,
)

from .. import seed_cache as cached
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import LATA, MEENA, PRIYA, SUNITA, VIKRAM, as_user
from .test_cooking import CHECKLIST, ON_THE_DAY
from .tiffins import EVENING, T, kitchen, tag, tiffin_order, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

ARJUN = "+919800001006"  # rider role at the seed hub (no partner record until a test makes one)
KAVITA = "+919800001007"  # packer
RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"
WEEK = f"{T.isocalendar().year}-W{T.isocalendar().week:02d}"


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


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def field_partner(db: AsyncSession, phone: str, kind: str) -> Partner:
    return await partners_service.verify(
        db, user_id=await user_id(db, phone), kind=kind, hub_id=SEED_HUB_ID, display_name=kind
    )


async def payable(db: AsyncSession, partner: Partner) -> int:
    account = await ledger.find_account(db, "partner_payable", "partner", partner.id)
    return 0 if account is None else await ledger.balance(db, account)


async def deliver_events(db: AsyncSession) -> None:
    await db.commit()
    while (await relay(db)).sent:
        pass


async def box_order(db: AsyncSession, phone: str, day: date = T) -> uuid.UUID:
    """A paid, locked box of the seed household, ready to be packed."""
    owner = await user_id(db, phone)
    home = await identity_api.household_for_user(db, owner)
    assert home is not None
    slot = await order_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Box {uuid.uuid4().hex[:6]}",
        kind="box",
        start=time(8, 0),
        end=time(9, 0),
        capacity=50,
    )
    product = (await catalog_api.product_ids_by_slug(db, ["tomato"]))["tomato"]
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=home.id,
            hub_id=SEED_HUB_ID,
            society_id=home.society_id,
            delivery_date=day,
            slot_id=slot.id,
            lines=(orders_api.CycleLine(product, 2, 6_000),),
        ),
    )
    await credit_topup(
        db, owner, order.total_paise, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await billing_api.debit_for_order(db, owner, order.id, order.total_paise, day)
    await orders_api.transition(db, order.id, "locked", actor="system")
    return order.id


async def test_each_basis_pays_its_partner_once_at_the_rate_of_the_day(
    db_session: AsyncSession,
) -> None:
    arjun = await field_partner(db_session, ARJUN, "rider")
    kavita = await field_partner(db_session, KAVITA, "packer")
    meena = await partners_service.for_user(db_session, await user_id(db_session, MEENA))
    (captain,) = meena
    arjun_user, kavita_user, meena_user = arjun.user_id, kavita.user_id, captain.user_id
    # A rate scheduled for the day after replaces the default only from then on.
    db_session.add(
        RateCard(
            hub_id=SEED_HUB_ID,
            kind="rider",
            basis="dispatched_order",
            amount_paise=900,
            valid_from=T + timedelta(days=1),
        )
    )
    box = await box_order(db_session, PRIYA)
    stranger = uuid.uuid4()

    await orders_api.transition(
        db_session, box, "picking", actor="packer", actor_user_id=kavita_user
    )
    await orders_api.transition(
        db_session, box, "packed", actor="packer", actor_user_id=kavita_user
    )
    await orders_api.transition(
        db_session, box, "dispatched", actor="rider", actor_user_id=arjun_user
    )
    await orders_api.transition(
        db_session, box, "at_society", actor="captain", actor_user_id=meena_user
    )
    await orders_api.transition(
        db_session, box, "delivered", actor="captain", actor_user_id=meena_user
    )
    await deliver_events(db_session)

    assert await payable(db_session, kavita) == 400  # per packed order
    assert await payable(db_session, arjun) == 800  # the rate of T, not the one from T + 1
    assert await payable(db_session, captain) == 600  # on delivered, not at_society

    # A redelivered event posts nothing new: the ledger keys the earning by partner, basis, order.
    events = (
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type.in_(
                        ("orders.packed", "orders.dispatched", "orders.delivered")
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    for event in events:
        await earnings.on_order_event(db_session, event)
    assert (await payable(db_session, kavita), await payable(db_session, arjun)) == (400, 800)
    assert await payable(db_session, captain) == 600

    # Nobody else earns: a person without a record of the kind, the hub and the system.
    other = await box_order(db_session, PRIYA)
    await orders_api.transition(
        db_session, other, "picking", actor="hub", actor_user_id=kavita_user
    )
    await orders_api.transition(db_session, other, "packed", actor="hub", actor_user_id=kavita_user)
    await orders_api.transition(
        db_session, other, "dispatched", actor="rider", actor_user_id=stranger
    )
    await orders_api.transition(db_session, other, "at_society", actor="hub")
    await orders_api.transition(db_session, other, "delivered", actor="hub")
    await deliver_events(db_session)
    assert (await payable(db_session, kavita), await payable(db_session, arjun)) == (400, 800)
    assert await payable(db_session, captain) == 600


async def test_a_rider_mode_delivery_pays_the_captain_nothing_and_a_zero_rate_posts_nothing(
    db_session: AsyncSession,
) -> None:
    arjun = await field_partner(db_session, ARJUN, "rider")
    (captain,) = await partners_service.for_user(db_session, await user_id(db_session, MEENA))
    db_session.add(
        RateCard(
            hub_id=SEED_HUB_ID,
            kind="rider",
            basis="dispatched_order",
            amount_paise=0,
            valid_from=T - timedelta(days=1),
        )
    )
    box = await box_order(db_session, PRIYA)
    await orders_api.transition(db_session, box, "picking", actor="hub")
    await orders_api.transition(db_session, box, "packed", actor="hub")
    await orders_api.transition(
        db_session, box, "dispatched", actor="rider", actor_user_id=arjun.user_id
    )
    await orders_api.transition(
        db_session, box, "at_society", actor="rider", actor_user_id=arjun.user_id
    )
    await orders_api.transition(
        db_session, box, "delivered", actor="rider", actor_user_id=arjun.user_id
    )

    await deliver_events(db_session)

    assert await payable(db_session, arjun) == 0  # a rate of 0 earns nothing, and no entry
    assert await ledger.find_account(db_session, "partner_payable", "partner", arjun.id) is None
    assert await payable(db_session, captain) == 0


async def test_a_kitchen_earns_her_portions_on_delivery_and_a_captain_earns_nothing_for_a_tiffin(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata_id = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    arjun = await field_partner(db_session, ARJUN, "rider")
    (captain,) = await partners_service.for_user(db_session, await user_id(db_session, MEENA))
    first = await tiffin_order(db_session, PRIYA, 2)
    second = await tiffin_order(db_session, MEENA, 1)
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    lata, _ = await as_user(api, google, LATA)
    (offer,) = (await api.get(f"/v1/assignments?date={T}", headers=lata)).json()["items"]
    await api.post(f"/v1/assignments/{offer['id']}/accept", headers={**lata, **key()})
    clock[0] = ON_THE_DAY
    ready = await api.post(
        f"/v1/assignments/{offer['id']}/ready",
        json={"checklist": CHECKLIST},
        headers={**lata, **key()},
    )
    assert ready.status_code == 200, ready.text
    for order in (first, second):
        await orders_api.transition(
            db_session, order, "dispatched", actor="rider", actor_user_id=arjun.user_id
        )
    await orders_api.transition(
        db_session, first, "delivered", actor="rider", actor_user_id=arjun.user_id
    )
    await orders_api.transition(
        db_session, second, "delivered", actor="captain", actor_user_id=captain.user_id
    )

    await deliver_events(db_session)

    cook = await db_session.get(Partner, lata_id)
    assert cook is not None
    assert await payable(db_session, cook) == 3 * 6_000  # three portions at ₹60
    assert await payable(db_session, arjun) == 2 * 800  # a pickup each, whoever delivers
    assert await payable(db_session, captain) == 0  # no tiffin basis for a captain (ADR-0027)

    clock[0] = datetime.now(UTC)  # the ledger stamps work with the database's clock
    week = await api.get("/v1/earnings", headers=lata)
    assert week.status_code == 200, week.text
    body = week.json()
    this_week = clock[0].astimezone(kitchens.IST).isocalendar()
    assert body["week"] == f"{this_week.year}-W{this_week.week:02d}"
    assert body["total_paise"] == 18_000 and body["payouts"] == []
    assert body["by_kind"] == [
        {"kind": "kitchen", "basis": "tiffin_portion", "units": 3, "amount_paise": 18_000}
    ]
    assert len(body["days"]) == 7 and sum(d["amount_paise"] for d in body["days"]) == 18_000
    assert sorted(e["units"] for e in body["entries"]) == [1, 2]
    assert {e["ref"]["type"] for e in body["entries"]} == {"order"}
    assert {e["ref"]["id"] for e in body["entries"]} == {str(first), str(second)}
    assert week.headers["Cache-Control"] == "no-store"
    # The view is the ledger: its totals equal what M5's facade says for her records.
    ledger_week = await billing_api.partner_week(db_session, [lata_id], body["week"])
    assert ledger_week.total_paise == body["total_paise"]


async def test_the_week_is_validated_and_a_partner_sees_her_own_records_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    await field_partner(db_session, ARJUN, "rider")
    arjun, _ = await as_user(api, google, ARJUN)
    priya, _ = await as_user(api, google, PRIYA)
    clock[0] = datetime(2027, 1, 13, 4, 0, tzinfo=UTC)  # Wednesday of 2027-W02

    this = await api.get("/v1/earnings", headers=arjun)
    last = await api.get("/v1/earnings?week=2027-W01", headers=arjun)
    assert this.json()["week"] == "2027-W02" and (this.json()["from"], this.json()["to"]) == (
        "2027-01-11",
        "2027-01-17",
    )
    assert last.json()["total_paise"] == 0 and len(last.json()["days"]) == 7
    for bad in ("2027-W03", "2027-W1", "2027-W54", "2027-W00", "27-W02", "banana", "2027-02"):
        assert code(await api.get(f"/v1/earnings?week={bad}", headers=arjun)) == (
            422,
            "partners.week_invalid",
        )
    # A customer who is no partner has no earnings view.
    assert (await api.get("/v1/earnings", headers=priya)).status_code == 403
    assert (await api.get("/v1/earnings")).status_code == 401


async def test_rolling_earnings_reach_the_solver_as_earnings_paise(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    sunita = await kitchen(db_session, SUNITA, "Sunita tai", lunch=5)
    await tiffin_order(db_session, PRIYA, 2)
    inside = datetime.combine(T - timedelta(days=10), time(12), UTC)
    outside = datetime.combine(T - timedelta(days=40), time(12), UTC)
    for when, amount in ((inside, 12_000), (outside, 6_000)):
        await billing_api.accrue_earning(
            db_session,
            partner_id=lata,
            basis="tiffin_portion",
            ref_type="order",
            ref_id=uuid.uuid4(),
            units=amount // 6_000,
            amount_paise=amount,
            earned_at=when,
        )
    await billing_api.accrue_earning(  # another basis does not count
        db_session,
        partner_id=sunita,
        basis="bag_delivered",
        ref_type="order",
        ref_id=uuid.uuid4(),
        units=1,
        amount_paise=600,
        earned_at=inside,
    )
    seen: list[intel_api.AssignRequest] = []

    async def no_answer(
        http: httpx2.AsyncClient, settings: Any, request: intel_api.AssignRequest, **kw: Any
    ) -> None:
        seen.append(request)

    monkeypatch.setattr(intel_api, "assign_kitchens", no_answer)
    ran = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert ran.status_code == 200, ran.text
    (request,) = seen
    assert {k.id: k.earnings_paise for k in request.kitchens} == {
        str(lata): 12_000,
        str(sunita): 0,
    }


async def test_an_event_that_is_not_an_order_events_shape_pays_nothing_and_raises_nothing(
    db_session: AsyncSession,
) -> None:
    for payload in ({}, {"order_id": str(uuid.uuid4())}, {"order_id": "x", "delivery_date": "y"}):
        for event_type in ("orders.delivered", "orders.dispatched", "orders.packed"):
            event = OutboxEvent(id=uuid.uuid4(), event_type=event_type, payload=payload)
            await earnings.on_order_event(db_session, event)


async def dispatched_events(db: AsyncSession, order: uuid.UUID) -> list[OutboxEvent]:
    rows = await db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == "orders.dispatched")
        .order_by(OutboxEvent.id)
    )
    return [e for e in rows.scalars() if e.payload["order_id"] == str(order)]


async def redelivered_box(
    db: AsyncSession, first_rider: uuid.UUID, second_rider: uuid.UUID
) -> uuid.UUID:
    """A box dispatched, held at the society, missed, and dispatched again for its free
    redelivery by another (or the same) rider."""
    box = await box_order(db, PRIYA)
    await orders_api.transition(db, box, "picking", actor="hub")
    await orders_api.transition(db, box, "packed", actor="hub")
    await orders_api.transition(db, box, "dispatched", actor="rider", actor_user_id=first_rider)
    await orders_api.transition(db, box, "at_society", actor="rider", actor_user_id=first_rider)
    await orders_api.transition(db, box, "undelivered", actor="rider", actor_user_id=first_rider)
    await orders_api.transition(db, box, "redelivery", actor="hub")
    await orders_api.transition(db, box, "dispatched", actor="rider", actor_user_id=second_rider)
    return box


async def test_a_box_dispatched_again_pays_each_trip_once_whatever_the_order_of_events_or_rates(
    db_session: AsyncSession, clock: list[datetime]
) -> None:
    arjun = await field_partner(db_session, ARJUN, "rider")
    sunita = await field_partner(db_session, SUNITA, "rider")
    box = await redelivered_box(db_session, arjun.user_id, sunita.user_id)
    first, second = await dispatched_events(db_session, box)
    assert first.payload["order_event_id"] != second.payload["order_event_id"]

    # The redelivery's event arrives first: it pays the second rider, not the first.
    await earnings.on_order_event(db_session, second)
    assert (await payable(db_session, sunita), await payable(db_session, arjun)) == (800, 0)

    # A rate change in between does not poison the first trip's event, which pays Arjun late.
    db_session.add(
        RateCard(
            hub_id=SEED_HUB_ID,
            kind="rider",
            basis="dispatched_order",
            amount_paise=900,
            valid_from=T - timedelta(days=1),
        )
    )
    await db_session.flush()
    await earnings.on_order_event(db_session, first)
    assert (await payable(db_session, sunita), await payable(db_session, arjun)) == (800, 900)

    # Replays, even with the rate changed again, pay nothing and never raise.
    db_session.add(
        RateCard(
            hub_id=SEED_HUB_ID,
            kind="rider",
            basis="dispatched_order",
            amount_paise=1_000,
            valid_from=T,
        )
    )
    await db_session.flush()
    for event in (first, second, first):
        await earnings.on_order_event(db_session, event)
    assert (await payable(db_session, sunita), await payable(db_session, arjun)) == (800, 900)


async def test_the_same_rider_on_both_trips_is_paid_for_both(
    db_session: AsyncSession, clock: list[datetime]
) -> None:
    arjun = await field_partner(db_session, ARJUN, "rider")
    box = await redelivered_box(db_session, arjun.user_id, arjun.user_id)

    await deliver_events(db_session)
    for event in await dispatched_events(db_session, box):  # delivered again: still once each
        await earnings.on_order_event(db_session, event)

    assert await payable(db_session, arjun) == 2 * 800
