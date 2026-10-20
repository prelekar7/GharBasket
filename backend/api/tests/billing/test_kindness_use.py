"""Kindness points, the customer's side and the rest of M5 §3.6: the balance with its expiry dates,
the history, donating meals, the daily expiry, and M9's `community.donation_verified`."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import kindness, service
from gharbasket.modules.billing.events import POINTS_DONATED
from gharbasket.modules.billing.jobs import DONATION_VERIFIED, on_donation_verified
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, PointsLot, Posting
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish, relay

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

PHONE = "+919800009301"


async def customer(clients: ClientFactory, google: FakeGoogle) -> tuple:
    api = clients()
    signed = await sign_in(api, google, phone_number=PHONE, sub=f"uid{PHONE}")
    return api, bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def ledger_points(session: AsyncSession, user_id: uuid.UUID) -> int:
    account = await service.find_account(session, "kindness", "user", user_id, "KINDNESS_PTS")
    return 0 if account is None else await service.balance(session, account)


async def lots_of(session: AsyncSession, user_id: uuid.UUID) -> list[PointsLot]:
    session.expire_all()
    rows = await session.execute(
        select(PointsLot).where(PointsLot.user_id == user_id).order_by(PointsLot.expires_at)
    )
    return list(rows.scalars())


async def credit(
    session: AsyncSession, user_id: uuid.UUID, points: int, expires_at: datetime | None = None
) -> uuid.UUID:
    """Credit a donation's points; optionally move its lot's expiry. Returns the source id."""
    source = uuid.uuid4()
    entry = await billing_api.credit_points(session, user_id, points, "donation", source)
    if expires_at is not None:
        await session.execute(
            update(PointsLot).where(PointsLot.entry_id == entry.id).values(expires_at=expires_at)
        )
    return source


async def donated_events(session: AsyncSession, user_id: uuid.UUID) -> list[dict]:
    rows = await session.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == POINTS_DONATED).order_by(OutboxEvent.id)
    )
    return [e.payload for e in rows.scalars() if e.payload["user_id"] == str(user_id)]


async def test_a_new_customer_has_no_points_and_the_goal_of_a_meal(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, _ = await customer(clients, google)

    balance = await api.get("/v1/kindness/balance", headers=auth)
    history = await api.get("/v1/kindness/entries", headers=auth)

    assert balance.status_code == 200
    assert balance.headers["cache-control"] == "no-store"
    assert balance.json() == {
        "points": 0,
        "expiring": [],
        "goal": {"points": 500, "code": "child_meal"},
    }
    assert history.json() == {"items": [], "next_cursor": None}
    assert (await api.get("/v1/kindness/balance")).status_code == 401
    assert (await api.get("/v1/kindness/entries")).status_code == 401


async def test_the_balance_lists_the_next_three_expiry_dates_within_90_days(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google)
    now = datetime.now(UTC)
    for points, days in ((10, 5), (20, 5), (30, 20), (40, 40), (50, 60), (60, 200)):
        await credit(db_session, user, points, now + timedelta(days=days))
    await credit(db_session, user, 70, now - timedelta(minutes=5))  # expired, not yet taken back

    body = (await api.get("/v1/kindness/balance", headers=auth)).json()

    def on(days: int) -> str:
        return (now + timedelta(days=days)).astimezone(kindness.IST).date().isoformat()

    assert body["points"] == 210
    assert body["expiring"] == [
        {"points": 30, "on": on(5)},
        {"points": 30, "on": on(20)},
        {"points": 40, "on": on(40)},
    ]
    assert await ledger_points(db_session, user) == 280  # the job takes the 70 back


async def test_the_history_comes_newest_first_with_refs_and_expiry(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google)
    referral, donation = uuid.uuid4(), uuid.uuid4()
    await billing_api.credit_points(db_session, user, 250, "referral", referral)
    await billing_api.credit_points(db_session, user, 300, "donation", donation)
    await kindness.donate(db_session, user, 1, 500, "k-history")

    first = (await api.get("/v1/kindness/entries?limit=2", headers=auth)).json()
    rest = (
        await api.get(f"/v1/kindness/entries?cursor={first['next_cursor']}", headers=auth)
    ).json()

    expires_on = kindness.expiry(datetime.now(UTC)).astimezone(kindness.IST).date().isoformat()
    items = first["items"] + rest["items"]
    assert [(e["kind"], e["points"], e["ref"], e["expires_on"]) for e in items] == [
        ("points_donated", -500, None, None),
        ("points_earned", 300, {"type": "donation", "id": str(donation)}, expires_on),
        ("points_earned", 250, {"type": "referral", "id": str(referral)}, expires_on),
    ]
    assert rest["next_cursor"] is None
    bad = await api.get("/v1/kindness/entries?cursor=bm9wZQ", headers=auth)
    assert bad.status_code == 422


async def test_donating_takes_meals_times_the_goal_from_the_lots_that_expire_first(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, auth, user = await customer(clients, google)
    now = datetime.now(UTC)
    await credit(db_session, user, 400, now + timedelta(days=300))
    await credit(db_session, user, 300, now + timedelta(days=10))
    await credit(db_session, user, 600, now + timedelta(days=100))
    await credit(db_session, user, 900, now - timedelta(hours=1))  # expired: never donated
    await db_session.commit()
    headers = {**auth, "Idempotency-Key": "donate-1"}

    short = await api.post(
        "/v1/kindness/donations", json={"meals": 3}, headers={**auth, "Idempotency-Key": "d0"}
    )
    assert short.status_code == 409
    assert short.json()["code"] == "billing.not_enough_points"
    assert (short.json()["points"], short.json()["needed"]) == (1300, 1500)

    given = await api.post("/v1/kindness/donations", json={"meals": 2}, headers=headers)
    again = await api.post("/v1/kindness/donations", json={"meals": 2}, headers=headers)

    assert given.status_code == 201, given.text
    body = given.json()
    assert (body["meals"], body["points"], body["code"]) == (2, 1000, "child_meal")
    assert again.status_code == 201 and again.json() == body
    assert again.headers["idempotent-replayed"] == "true"
    remaining = [(lot.points, lot.remaining) for lot in await lots_of(db_session, user)]
    assert remaining == [(900, 900), (300, 0), (600, 0), (400, 300)]
    assert (await api.get("/v1/kindness/balance", headers=auth)).json()["points"] == 300
    entry = await db_session.get(JournalEntry, uuid.UUID(body["id"]))
    assert entry is not None and entry.kind == "points_donated"
    assert entry.idempotency_key == f"points_donation:{user}:donate-1"
    assert await donated_events(db_session, user) == [
        {
            "entry_id": body["id"],
            "user_id": str(user),
            "meals": 2,
            "points": 1000,
            "code": "child_meal",
        }
    ]


async def test_donation_requests_are_checked(clients: ClientFactory, google: FakeGoogle) -> None:
    api, auth, _ = await customer(clients, google)
    for meals in (0, 11, "two"):
        answer = await api.post(
            "/v1/kindness/donations",
            json={"meals": meals},
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert answer.status_code == 422
    no_key = await api.post("/v1/kindness/donations", json={"meals": 1}, headers=auth)
    assert no_key.status_code in (400, 422, 428)
    nobody = await api.post(
        "/v1/kindness/donations", json={"meals": 1}, headers={"Idempotency-Key": "x"}
    )
    assert nobody.status_code == 401


async def test_a_replayed_donation_key_posts_once_even_after_the_request_key_expired(
    db_session: AsyncSession,
) -> None:
    user = uuid.uuid4()
    await credit(db_session, user, 1200)
    long_key = "k" * 200

    first = await kindness.donate(db_session, user, 1, 500, long_key)
    again = await kindness.donate(db_session, user, 1, 500, long_key)

    assert first.id == again.id
    assert first.idempotency_key.startswith(f"points_donation:{user}:sha256:")
    assert len(first.idempotency_key) <= 160
    assert await ledger_points(db_session, user) == 700
    assert len(await donated_events(db_session, user)) == 1
    with pytest.raises(ValueError):
        await kindness.donate(db_session, user, 0, 500, "zero")


async def test_expiry_takes_back_what_is_left_of_each_expired_lot_once(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api, _, user = await customer(clients, google)
    other = uuid.uuid4()
    run_at = datetime(2027, 10, 12, 19, 0, tzinfo=UTC)  # 13 Oct 00:30 IST
    yesterday = datetime(2027, 10, 12, 18, 29, 59, tzinfo=UTC)  # 12 Oct 23:59:59 IST
    old = await credit(db_session, user, 600, yesterday - timedelta(days=3))
    await credit(db_session, user, 300, yesterday)
    await credit(db_session, user, 200, run_at + timedelta(days=1))
    await credit(db_session, other, 50, yesterday)
    # Half of the oldest lot was donated earlier.
    await db_session.execute(
        update(PointsLot).where(PointsLot.source_id == old).values(remaining=100)
    )
    account = await service.find_account(db_session, "kindness", "user", user, "KINDNESS_PTS")
    assert account is not None
    expense = await service.platform_account(db_session, "kindness_expense", "KINDNESS_PTS")
    await service.post(
        db_session,
        kind="points_donated",
        key=f"test-donation:{user}",
        legs=[service.Leg(account, 500), service.Leg(expense, -500)],
    )
    await db_session.commit()

    first = await api.post(
        "/v1/internal/jobs/expire_kindness_points", json={"scheduled_for": run_at.isoformat()}
    )
    second = await api.post(
        "/v1/internal/jobs/expire_kindness_points", json={"scheduled_for": run_at.isoformat()}
    )

    assert first.status_code == 200, first.text
    assert first.json()["summary"] == {"users": 2, "lots": 3, "points": 450}
    assert second.json()["summary"] == {"users": 0, "lots": 0, "points": 0}
    assert [lot.remaining for lot in await lots_of(db_session, user)] == [0, 0, 200]
    assert await ledger_points(db_session, user) == 200
    assert await ledger_points(db_session, other) == 0
    expired = (
        await db_session.execute(
            select(JournalEntry).where(
                JournalEntry.kind == "points_expired", JournalEntry.ref_id == old
            )
        )
    ).scalar_one()
    assert expired.ref_type == "donation" and expired.idempotency_key.startswith("points_expiry:")
    inr = (
        await db_session.execute(
            select(func.count())
            .select_from(Posting)
            .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
            .join(JournalEntry, JournalEntry.id == Posting.entry_id)
            .where(
                LedgerAccount.currency == "INR",
                JournalEntry.kind.in_(("points_expired", "points_donated", "points_earned")),
            )
        )
    ).scalar_one()
    assert inr == 0


async def test_a_verified_donation_credits_its_points_once(db_session: AsyncSession) -> None:
    user, donation = uuid.uuid4(), uuid.uuid4()
    payload = {
        "donation_id": str(donation),
        "user_id": str(user),
        "household_id": str(uuid.uuid4()),
        "hub_id": str(uuid.uuid4()),
        "society_id": str(uuid.uuid4()),
        "drive_id": None,
        "points": 240,
    }
    await publish(db_session, DONATION_VERIFIED, payload)
    await publish(db_session, DONATION_VERIFIED, payload)  # delivered twice

    await relay(db_session)

    assert await ledger_points(db_session, user) == 240
    lots = await lots_of(db_session, user)
    assert [(lot.points, lot.source_type, lot.source_id) for lot in lots] == [
        (240, "donation", donation)
    ]
    pending = await db_session.execute(
        select(func.count()).where(
            OutboxEvent.event_type == DONATION_VERIFIED, OutboxEvent.sent_at.is_(None)
        )
    )
    assert pending.scalar_one() == 0


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"donation_id": "not-a-uuid", "user_id": str(uuid.uuid4()), "points": 10},
        {"donation_id": str(uuid.uuid4()), "user_id": None, "points": 10},
        {"donation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4())},
        {"donation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "points": "10"},
        {"donation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "points": True},
        {"donation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "points": 2.5},
        {"donation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "points": -5},
        {"donation_id": str(uuid.uuid4()), "user_id": str(uuid.uuid4()), "points": 0},
    ],
)
async def test_an_unusable_donation_event_is_skipped_without_raising(
    db_session: AsyncSession, payload: dict
) -> None:
    before = (await db_session.execute(select(func.count()).select_from(PointsLot))).scalar_one()

    await on_donation_verified(
        db_session, OutboxEvent(event_type=DONATION_VERIFIED, payload=payload)
    )
    await on_donation_verified(db_session, OutboxEvent(event_type=DONATION_VERIFIED, payload=[1]))

    after = (await db_session.execute(select(func.count()).select_from(PointsLot))).scalar_one()
    assert after == before


async def test_the_same_donation_with_other_points_is_logged_not_raised(
    db_session: AsyncSession,
) -> None:
    user, donation = uuid.uuid4(), uuid.uuid4()
    payload = {"donation_id": str(donation), "user_id": str(user), "points": 100}
    await on_donation_verified(
        db_session, OutboxEvent(event_type=DONATION_VERIFIED, payload=payload)
    )

    changed = {**payload, "points": 150}
    await on_donation_verified(
        db_session, OutboxEvent(event_type=DONATION_VERIFIED, payload=changed)
    )

    assert await ledger_points(db_session, user) == 100
