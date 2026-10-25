"""The hub cut-off monitor and delivery dates (M3 §3.4): shapes and defaults from the API's clock,
counts that never double-count a household, the `cutoff_lock` run, the unpaid boxes with the
wallet read live, and who may see what."""

import uuid
from datetime import UTC, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.orders.api import PaymentFailedOrder
from gharbasket.modules.orders.jobs import on_wallet_topped_up
from gharbasket.modules.subscriptions import cutoff as hub_cutoff
from gharbasket.modules.subscriptions.models import WeeklyCycle
from gharbasket.platform.models import JobRun, OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.test_cutoff import CUTOFF_AT, GRACE_END, SUNITA, approved_box, run
from .test_cycles import MEENA, NOW, PRIYA, VIKRAM, as_user, subscribe
from .test_edits import key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

RAMESH = "+919800001003"
SAVITA = "+919800001012"
SANJAY = "+919800001009"  # franchise owner of the seed hub
KAVITA = "+919800001007"  # packer at the seed hub
NEHA = "+919800001011"  # admin
TUESDAY = "2026-10-13"
CUTOFF_UTC = "2026-10-12T15:30:00Z"
GRACE_UTC = "2026-10-12T17:30:00Z"
MON_EVENING = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)  # Mon 20:00 IST
MON_AFTER_CUTOFF = datetime(2026, 10, 12, 15, 31, tzinfo=UTC)  # Mon 21:01 IST
DATES = f"/v1/hubs/{SEED_HUB_ID}/delivery-dates"
CUTOFF = f"/v1/hubs/{SEED_HUB_ID}/cutoff"


async def at(
    clock: datetime, api: httpx2.AsyncClient, google: FakeGoogle, phone: str, path: str, **headers
) -> httpx2.Response:
    """GET as `phone`, signed in at the moved clock (tokens are short-lived)."""
    with time_machine.travel(clock, tick=False):
        auth, _ = await as_user(api, google, phone)
        return await api.get(path, headers={**auth, **headers})


# --- delivery dates -------------------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_delivery_dates_list_the_days_a_society_of_the_hub_delivers(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)

    default = await api.get(DATES, headers=manager)  # Saturday 10 October, 15:30 IST
    short = await api.get(DATES, params={"from": "2026-10-14", "days": 7}, headers=manager)
    one = await api.get(DATES, params={"from": TUESDAY, "days": 1}, headers=manager)
    none = await api.get(DATES, params={"from": "2026-10-14", "days": 6}, headers=manager)

    assert default.status_code == 200, default.text
    assert default.headers["Cache-Control"] == "no-store"
    assert default.json() == {
        "hub_id": str(SEED_HUB_ID),
        "from": "2026-10-10",
        "to": "2026-10-23",  # 14 calendar days, the first being today
        "items": [
            {"date": TUESDAY, "societies": 5, "cutoff_at": CUTOFF_UTC},
            {"date": "2026-10-20", "societies": 5, "cutoff_at": "2026-10-19T15:30:00Z"},
        ],
    }
    assert [i["date"] for i in short.json()["items"]] == ["2026-10-20"]
    assert short.json()["to"] == "2026-10-20"
    assert [i["date"] for i in one.json()["items"]] == [TUESDAY]
    assert none.json()["items"] == [] and none.json()["to"] == "2026-10-19"


async def test_delivery_dates_start_from_the_ist_day_of_the_api_clock(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    # 19:00 UTC on Monday is 00:30 IST on Tuesday: today is already the delivery day.
    moved = datetime(2026, 10, 12, 19, 0, tzinfo=UTC)

    answer = await at(moved, api, google, VIKRAM, DATES, **{})

    assert answer.json()["from"] == TUESDAY
    assert answer.json()["items"][0]["date"] == TUESDAY


async def test_a_hub_without_delivery_days_has_no_dates_and_no_monitor(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    admin, _ = await as_user(api, google, NEHA)
    empty = f"/v1/hubs/{uuid.uuid4()}"

    dates = await api.get(f"{empty}/delivery-dates", headers=admin)
    monitor = await api.get(f"{empty}/cutoff", headers=admin)

    assert (dates.status_code, dates.json()["items"]) == (200, [])
    assert (monitor.status_code, monitor.json()["code"]) == (404, "subscriptions.not_delivery_date")


@pytest.mark.parametrize("days", [0, 32, -1])
async def test_days_outside_one_to_thirty_one_are_refused(
    clients: ClientFactory, google: FakeGoogle, days: int
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)

    answer = await api.get(DATES, params={"days": days}, headers=manager)

    assert (answer.status_code, answer.json()["code"]) == (422, "request.invalid")


async def test_a_month_of_delivery_dates_is_allowed(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)

    answer = await api.get(DATES, params={"from": "2026-10-01", "days": 31}, headers=manager)

    assert answer.status_code == 200 and answer.json()["to"] == "2026-10-31"
    assert [i["date"] for i in answer.json()["items"]] == [
        "2026-10-06",
        "2026-10-13",
        "2026-10-20",
        "2026-10-27",
    ]


# --- who may see them -----------------------------------------------------------------------------


async def test_delivery_dates_go_to_hub_staff_and_admin_only_for_their_hub(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    answers: dict[str, int] = {}
    for name, phone in {
        "manager": VIKRAM,
        "owner": SANJAY,
        "packer": KAVITA,
        "admin": NEHA,
        "customer": PRIYA,
    }.items():
        auth, _ = await as_user(api, google, phone)
        answers[name] = (await api.get(DATES, headers=auth)).status_code
    manager, _ = await as_user(api, google, VIKRAM)
    elsewhere = await api.get(f"/v1/hubs/{uuid.uuid4()}/delivery-dates", headers=manager)
    as_manager = await api.get(
        DATES, headers={**manager, "X-Active-Role": f"hub_manager:{SEED_HUB_ID}"}
    )
    anonymous = await api.get(DATES)

    assert answers == {"manager": 200, "owner": 200, "packer": 200, "admin": 200, "customer": 403}
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (403, "auth.forbidden")
    assert as_manager.status_code == 200 and anonymous.status_code == 401


async def test_the_monitor_goes_to_managers_owners_and_admin_not_packers_or_customers(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    answers: dict[str, int] = {}
    for name, phone in {
        "manager": VIKRAM,
        "owner": SANJAY,
        "admin": NEHA,
        "packer": KAVITA,
        "customer": PRIYA,
    }.items():
        auth, _ = await as_user(api, google, phone)
        answers[name] = (await api.get(CUTOFF, params={"date": TUESDAY}, headers=auth)).status_code
    manager, _ = await as_user(api, google, VIKRAM)
    elsewhere = await api.get(
        f"/v1/hubs/{uuid.uuid4()}/cutoff", params={"date": TUESDAY}, headers=manager
    )

    assert answers == {"manager": 200, "owner": 200, "admin": 200, "packer": 403, "customer": 403}
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (403, "auth.forbidden")


# --- the monitor: dates ---------------------------------------------------------------------------


async def test_the_monitor_defaults_to_the_next_delivery_date_before_and_after_the_cutoff(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    expected = {
        datetime(2026, 10, 10, 10, 0, tzinfo=UTC): TUESDAY,  # Saturday
        datetime(2026, 10, 12, 15, 29, tzinfo=UTC): TUESDAY,  # Mon 20:59 IST: tonight's cut-off
        datetime(2026, 10, 12, 15, 31, tzinfo=UTC): TUESDAY,  # Mon 21:01 IST: just locked
        datetime(2026, 10, 12, 19, 0, tzinfo=UTC): "2026-10-20",  # Tue 00:30 IST: today's is done
    }

    got = {}
    for clock in expected:
        answer = await at(clock, api, google, VIKRAM, CUTOFF)
        assert answer.status_code == 200, answer.text
        got[clock] = answer.json()["delivery_date"]
        assert answer.json()["as_of"] == clock.strftime("%Y-%m-%dT%H:%M:%SZ")

    assert got == expected


async def test_the_monitor_answers_for_a_chosen_date_and_refuses_a_day_without_deliveries(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)

    chosen = await api.get(CUTOFF, params={"date": "2026-10-20"}, headers=manager)
    monday = await api.get(CUTOFF, params={"date": "2026-10-19"}, headers=manager)
    bad = await api.get(CUTOFF, params={"date": "20-10-2026"}, headers=manager)

    body = chosen.json()
    assert chosen.status_code == 200 and chosen.headers["Cache-Control"] == "no-store"
    assert (body["hub_id"], body["delivery_date"]) == (str(SEED_HUB_ID), "2026-10-20")
    assert (body["cutoff_at"], body["grace_until"]) == (
        "2026-10-19T15:30:00Z",
        "2026-10-19T17:30:00Z",
    )
    assert set(body) == {
        "hub_id",
        "delivery_date",
        "cutoff_at",
        "grace_until",
        "as_of",
        "counts",
        "last_run",
        "short",
    }
    assert body["counts"] == {
        "awaiting": 0,
        "approved": 0,
        "auto_approved": 0,
        "skipped": 0,
        "locked": 0,
        "payment_needed": 0,
    }
    assert body["last_run"] is None and body["short"] == []
    assert (monday.status_code, monday.json()["code"]) == (404, "subscriptions.not_delivery_date")
    assert (bad.status_code, bad.json()["code"]) == (422, "request.invalid")


# --- the monitor: counts, the run and the unpaid ---------------------------------------------


async def week(
    api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession
) -> dict[str, tuple[uuid.UUID, uuid.UUID, int, uuid.UUID | None]]:
    """Five households' Tuesday: Priya, Meena and Sunita approved (Priya by auto-approve), Ramesh
    awaiting, Savita skipped. {phone: (user id, cycle id, order total, order id)}."""
    users = {phone: await as_user(api, google, phone) for phone in (PRIYA, MEENA, SUNITA)}
    ramesh, ramesh_id = await as_user(api, google, RAMESH)
    savita, savita_id = await as_user(api, google, SAVITA)
    await subscribe(api, ramesh)
    await subscribe(api, savita)
    out: dict[str, tuple[uuid.UUID, uuid.UUID, int, uuid.UUID | None]] = {}
    for phone, (auth, user_id) in users.items():
        cycle, order, total = await approved_box(api, auth)
        out[phone] = (user_id, cycle, total, order)
    await db.execute(
        update(WeeklyCycle).where(WeeklyCycle.id == out[PRIYA][1]).values(auto_approved=True)
    )
    cycles = (await api.get("/v1/cycles/current", headers=ramesh)).json()
    out[RAMESH] = (ramesh_id, uuid.UUID(cycles["id"]), 0, None)
    current = (await api.get("/v1/cycles/current", headers=savita)).json()
    skipped = await api.post(f"/v1/cycles/{current['id']}/skip", headers={**savita, **key()})
    assert skipped.status_code == 200, skipped.text
    out[SAVITA] = (savita_id, uuid.UUID(current["id"]), 0, None)
    await db.commit()
    return out


@time_machine.travel(NOW, tick=False)
async def test_counts_never_double_count_and_the_unpaid_are_listed_with_the_wallet_live(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boxes = await week(api, google, db_session)
    priya_id, _, priya_total, _ = boxes[PRIYA]
    meena_id, _, meena_total, meena_order = boxes[MEENA]
    sunita_id, _, sunita_total, sunita_order = boxes[SUNITA]

    before = await at(MON_EVENING, api, google, VIKRAM, CUTOFF)
    assert before.status_code == 200, before.text
    assert before.json()["counts"] == {
        "awaiting": 1,
        "approved": 3,
        "auto_approved": 1,
        "skipped": 1,
        "locked": 0,
        "payment_needed": 0,
    }
    assert before.json()["last_run"] is None and before.json()["short"] == []

    # The cut-off: Priya's wallet covers her box, Meena's falls short, Sunita's is empty.
    await credit_topup(db_session, priya_id, priya_total, payment_ref="p", topup_id=uuid.uuid4())
    await credit_topup(db_session, meena_id, 1_000, payment_ref="m", topup_id=uuid.uuid4())
    with time_machine.travel(datetime(2026, 10, 12, 15, 30, tzinfo=UTC), tick=False):
        summary = await run(api, "cutoff_lock", CUTOFF_AT)
    assert (summary["locked"], summary["payment_failed"]) == (1, 2)
    # The wallet is read live: Sunita topped up 500 after the cut-off, short of her box still.
    await credit_topup(db_session, sunita_id, 500, payment_ref="s", topup_id=uuid.uuid4())
    db_session.expire_all()

    after = await at(MON_AFTER_CUTOFF, api, google, VIKRAM, CUTOFF)
    body = after.json()
    # The cycles still say "approved" (the mirror has not run): only the order side counts them.
    counts = {
        "awaiting": 1,
        "approved": 0,
        "auto_approved": 1,
        "skipped": 1,
        "locked": 1,
        "payment_needed": 2,
    }
    assert after.status_code == 200 and body["counts"] == counts
    assert body["delivery_date"] == TUESDAY and body["grace_until"] == GRACE_UTC
    assert body["last_run"] == {
        "cutoff_on": "2026-10-12",
        "locked": 1,
        "total": 3,
        "duration_s": body["last_run"]["duration_s"],
        "grace_paid": 0,
        "cancelled": 0,
    }
    assert isinstance(body["last_run"]["duration_s"], int) and body["last_run"]["duration_s"] >= 0

    owed = {
        meena_order: ("Sunrise Towers", "B wing, B-402", meena_total, 1_000),
        sunita_order: ("Green Valley Heights", "B wing, B-504", sunita_total, 500),
    }
    expected = sorted(
        (
            {
                "order_id": str(order),
                "society": society,
                "flat": flat,
                "box_paise": total,
                "wallet_paise": wallet,
                "short_paise": total - wallet,
            }
            for order, (society, flat, total, wallet) in owed.items()
        ),
        key=lambda row: -row["short_paise"],
    )
    assert expected[0]["short_paise"] != expected[1]["short_paise"]
    assert body["short"] == expected
    # Never a name or a phone.
    text = after.text
    assert not any(word in text for word in ("Meena", "Sunita", "Priya", "+91", "98000010"))

    # Once the events are delivered the cycles mirror their orders: nothing moves.
    await relay(db_session)
    await db_session.commit()
    mirrored = await at(MON_AFTER_CUTOFF, api, google, VIKRAM, CUTOFF)
    assert mirrored.json()["counts"] == counts and mirrored.json()["short"] == expected
    cycles = (await db_session.execute(select(WeeklyCycle))).scalars()
    assert (
        sorted(c.status for c in cycles if c.status == "payment_failed") == ["payment_failed"] * 2
    )

    # Meena pays in the grace period; Sunita's grace ends unpaid.
    await credit_topup(db_session, meena_id, meena_total, payment_ref="m2", topup_id=uuid.uuid4())
    await on_wallet_topped_up(
        db_session,
        OutboxEvent(event_type="billing.wallet_topped_up", payload={"user_id": str(meena_id)}),
    )
    with time_machine.travel(datetime(2026, 10, 12, 17, 30, tzinfo=UTC), tick=False):
        await run(api, "payment_grace_expiry", GRACE_END)
    db_session.expire_all()
    done = await at(datetime(2026, 10, 12, 17, 31, tzinfo=UTC), api, google, VIKRAM, CUTOFF)
    assert done.json()["counts"] == {
        "awaiting": 1,
        "approved": 0,
        "auto_approved": 1,
        "skipped": 1,
        "locked": 2,
        "payment_needed": 0,
    }
    assert done.json()["last_run"]["grace_paid"] == 1
    assert done.json()["last_run"]["cancelled"] == 1
    assert done.json()["short"] == []


# --- the cut-off run ------------------------------------------------------------------------------


async def test_last_run_is_the_cutoff_lock_run_of_that_date_and_null_before_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)
    start = datetime(2026, 10, 12, 15, 30, 2, tzinfo=UTC)

    def row(**changes: object) -> JobRun:
        values: dict[str, object] = {
            "name": "cutoff_lock",
            "scheduled_for": datetime(2026, 10, 12, 15, 30, tzinfo=UTC),
            "started_at": start,
            "finished_at": datetime(2026, 10, 12, 15, 30, 6, tzinfo=UTC),
            "status": "ok",
            "summary": {
                "locked": 50,
                "payment_failed": 2,
                "skipped_already_locked": 0,
                "delivery_date": TUESDAY,
            },
        }
        return JobRun(**{**values, **changes})

    none = await api.get(CUTOFF, params={"date": TUESDAY}, headers=manager)
    db_session.add_all(
        [
            row(name="payment_grace_expiry"),  # another job
            row(status="failed", summary=None),  # a run that did not finish the work
            row(scheduled_for=datetime(2026, 10, 19, 15, 30, tzinfo=UTC)),  # another date
        ]
    )
    await db_session.commit()
    others = await api.get(CUTOFF, params={"date": TUESDAY}, headers=manager)
    db_session.add_all(
        [
            row(),
            # A re-run finds everything locked already: the first run is the one that shows.
            row(
                started_at=datetime(2026, 10, 12, 15, 40, tzinfo=UTC),
                finished_at=datetime(2026, 10, 12, 15, 40, 1, tzinfo=UTC),
                summary={"locked": 0, "payment_failed": 0, "skipped_already_locked": 52},
            ),
        ]
    )
    await db_session.commit()
    filled = await api.get(CUTOFF, params={"date": TUESDAY}, headers=manager)

    assert none.json()["last_run"] is None and others.json()["last_run"] is None
    assert filled.json()["last_run"] == {
        "cutoff_on": "2026-10-12",
        "locked": 50,
        "total": 52,
        "duration_s": 4,
        "grace_paid": 0,
        "cancelled": 0,
    }


async def test_dates_beyond_the_calendar_are_refused_not_a_server_error(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)

    for path, params in (
        (DATES, {"from": "9999-12-31", "days": 2}),
        (DATES, {"from": "0001-01-01"}),
        (CUTOFF, {"date": "0001-01-01"}),
        (CUTOFF, {"date": "9999-12-31"}),
    ):
        answer = await api.get(path, params=params, headers=manager)
        assert answer.status_code == 422, (params, answer.status_code)


async def test_the_unpaid_list_costs_the_same_queries_for_one_order_or_hundreds(
    db_session: AsyncSession,
) -> None:
    """Labels, owners and wallets are read for the whole list, not per order."""
    engine = db_session.bind.sync_engine  # pyright: ignore[reportOptionalMemberAccess]

    def unpaid(count: int) -> tuple[PaymentFailedOrder, ...]:
        society = uuid.uuid4()
        return tuple(
            PaymentFailedOrder(uuid.uuid4(), None, uuid.uuid4(), society, 50_000)
            for _ in range(count)
        )

    async def statements(failed: tuple[PaymentFailedOrder, ...]) -> int:
        seen: list[str] = []

        def record(*args: object) -> None:
            seen.append(str(args[2]))

        event.listen(engine, "before_cursor_execute", record)
        try:
            await hub_cutoff._short(db_session, failed)  # pyright: ignore[reportPrivateUsage]
        finally:
            event.remove(engine, "before_cursor_execute", record)
        return len(seen)

    one, many = await statements(unpaid(1)), await statements(unpaid(300))

    assert one == many <= 4


async def test_owners_of_households_are_read_in_one_query(db_session: AsyncSession) -> None:
    priya_id = await db_session.scalar(select(User.id).where(User.phone_e164 == PRIYA))
    assert priya_id is not None
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None

    owners = await identity_api.owners_of_households(db_session, [household.id, uuid.uuid4()])

    assert owners == {household.id: priya_id}
    assert await identity_api.owners_of_households(db_session, []) == {}
