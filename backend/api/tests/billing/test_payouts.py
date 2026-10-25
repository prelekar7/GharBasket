"""Partner payouts (M5 §3.5): `weekly_payouts` pays each eligible partner what she was owed up to
Sunday (IST) through the simulated provider, holds the others with M8's reason, never pays twice,
reverses a failure, and the endpoints show a partner her own payouts and a hub manager her hub's."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from pydantic import ValidationError
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import payouts, service
from gharbasket.modules.billing.earnings import week_bounds
from gharbasket.modules.billing.models import PAYOUT_SENT, JournalEntry, Payout
from gharbasket.modules.billing.payouts import ProviderAnswer
from gharbasket.modules.billing.wallet import IST
from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners.models import KycDocument, Partner
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, OutboxEvent

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from ..partners.payees import make_partner

pytestmark = [pytest.mark.anyio, pytest.mark.services]

# Monday 18 January 2027, 10:00 IST: the run that pays 2027-W02 (Monday 11 to Sunday 17 January).
RUN = datetime(2027, 1, 18, 4, 30, tzinfo=UTC)
WEEK = "2027-W02"
IN_WEEK = datetime(2027, 1, 13, 6, 0, tzinfo=UTC)  # Wednesday 11:30 IST
SUNDAY_LATE = datetime(2027, 1, 17, 18, 0, tzinfo=UTC)  # Sunday 23:30 IST: still the week
MONDAY_EARLY = datetime(2027, 1, 17, 18, 45, tzinfo=UTC)  # Monday 00:15 IST: the next week
HUB_A, HUB_B = uuid.uuid4(), uuid.uuid4()


async def earn(db: AsyncSession, partner_id: uuid.UUID, amount: int, at: datetime) -> None:
    await billing_api.accrue_earning(
        db,
        partner_id=partner_id,
        basis="packed_order",
        ref_type="order",
        ref_id=uuid.uuid4(),
        units=1,
        amount_paise=amount,
        earned_at=at,
    )


async def run(db: AsyncSession, at: datetime = RUN, **settings: Any) -> dict[str, Any]:
    done = await run_job(db, "weekly_payouts", settings=make_settings(**settings), scheduled_for=at)
    assert done.status == "ok", done.error
    return done.summary or {}


async def rows(db: AsyncSession, partner_id: uuid.UUID) -> list[Payout]:
    found = await db.execute(
        select(Payout).where(Payout.partner_id == partner_id).order_by(Payout.period_from)
    )
    return list(found.scalars())


async def payable(db: AsyncSession, partner_id: uuid.UUID) -> int:
    account = await service.find_account(db, "partner_payable", "partner", partner_id)
    return 0 if account is None else await service.balance(db, account)


async def clearing(db: AsyncSession) -> int:
    return await service.balance(db, await service.platform_account(db, "payout_clearing"))


async def entries(db: AsyncSession, payout_id: uuid.UUID) -> list[tuple[str, str]]:
    found = await db.execute(
        select(JournalEntry.kind, JournalEntry.idempotency_key)
        .where(JournalEntry.ref_type == "payout", JournalEntry.ref_id == payout_id)
        .order_by(JournalEntry.id)
    )
    return [(kind, key) for kind, key in found.all()]


async def sent_events(db: AsyncSession) -> list[dict[str, Any]]:
    found = await db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.event_type == "billing.payout_sent")
        .order_by(OutboxEvent.id)
    )
    return [event.payload for event in found.scalars()]


class FailingProvider:
    """The provider refuses every payout (a closed account, say)."""

    @property
    def name(self) -> str:
        return "simulated"

    async def send(self, session: AsyncSession, payout: Payout) -> ProviderAnswer:
        return ProviderAnswer("failed", f"pout_fail_{payout.id.hex}")


def failing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(payouts, "provider_for", lambda settings: FailingProvider())


# --- the weekly run --------------------------------------------------------------------------


async def test_an_eligible_partner_is_paid_her_ist_week_once_and_a_rerun_pays_nothing_twice(
    db_session: AsyncSession,
) -> None:
    partner = await make_partner(db_session, hub_id=HUB_A)
    await earn(db_session, partner.id, 40_000, IN_WEEK)
    await earn(db_session, partner.id, 2_400, SUNDAY_LATE)
    await earn(db_session, partner.id, 400, MONDAY_EARLY)  # earned after the cut: next week
    await earn(db_session, partner.id, 1_000, IN_WEEK - timedelta(days=7))  # a week earlier

    first = await run(db_session)
    again = await run(db_session, RUN + timedelta(minutes=5))

    assert first["week"] == WEEK
    assert (first["pending"], first["processed"], first["failed"]) == (1, 1, 0)
    assert (again["existing"], again["pending"], again["processed"]) == (1, 0, 0)
    (paid,) = await rows(db_session, partner.id)
    assert (paid.week, paid.period_from, paid.period_to) == (
        WEEK,
        date(2027, 1, 11),
        date(2027, 1, 17),
    )
    assert (paid.status, paid.amount_paise, paid.hold_reason, paid.hub_id) == (
        "processed",
        43_400,  # everything owed up to Sunday 23:59:59 IST, the earlier week's included
        None,
        HUB_A,
    )
    assert (paid.provider, paid.provider_ref) == ("simulated", f"pout_sim_{paid.id.hex}")
    assert paid.processed_at is not None and paid.attempts == 1
    assert await entries(db_session, paid.id) == [("payout", f"payout:{partner.id}:{WEEK}")]
    assert await payable(db_session, partner.id) == 400
    assert await clearing(db_session) == -43_400
    assert await sent_events(db_session) == [
        {
            "payout_id": str(paid.id),
            "partner_id": str(partner.id),
            "amount_paise": 43_400,
            "status": "processed",
        }
    ]


@pytest.mark.parametrize(
    ("at", "week"),
    [
        (RUN, "2027-W02"),
        (datetime(2027, 1, 17, 18, 31, tzinfo=UTC), "2027-W02"),  # Monday 00:01 IST
        (datetime(2027, 1, 17, 18, 29, tzinfo=UTC), "2027-W01"),  # Sunday 23:59 IST
        (datetime(2027, 1, 4, 4, 30, tzinfo=UTC), "2026-W53"),  # 2026 has 53 ISO weeks
        (datetime(2027, 1, 21, 9, 0, tzinfo=UTC), "2027-W02"),  # a late, manual run that week
    ],
)
def test_the_week_paid_is_the_ist_week_before_the_run(at: datetime, week: str) -> None:
    assert payouts.week_before(at) == week


async def test_the_week_ends_at_sunday_midnight_ist_not_utc(db_session: AsyncSession) -> None:
    partner = await make_partner(db_session)
    await earn(db_session, partner.id, 20_000, SUNDAY_LATE)  # 18:00 UTC Sunday, Sunday in IST
    await earn(db_session, partner.id, 30_000, MONDAY_EARLY)  # 18:45 UTC Sunday, Monday in IST

    await run(db_session)

    (paid,) = await rows(db_session, partner.id)
    assert paid.amount_paise == 20_000
    await run(db_session, RUN + timedelta(days=7))
    assert [p.amount_paise for p in await rows(db_session, partner.id)] == [20_000, 30_000]


async def test_under_100_rupees_is_carried_except_for_a_partner_who_left(
    db_session: AsyncSession,
) -> None:
    small = await make_partner(db_session)
    gone = await make_partner(db_session, status="left")
    await earn(db_session, small.id, 9_900, IN_WEEK)
    await earn(db_session, gone.id, 600, IN_WEEK)

    summary = await run(db_session)

    assert await rows(db_session, small.id) == []  # no row, carried to next week
    (paid,) = await rows(db_session, gone.id)
    assert (paid.status, paid.amount_paise) == ("processed", 600)  # left: still paid, all of it
    assert (summary["carried"], summary["pending"]) == (1, 1)
    await earn(db_session, small.id, 200, IN_WEEK + timedelta(days=7))
    await run(db_session, RUN + timedelta(days=7))
    (later,) = await rows(db_session, small.id)
    assert (later.week, later.amount_paise) == ("2027-W03", 10_100)
    assert await payable(db_session, small.id) == await payable(db_session, gone.id) == 0


async def test_each_reason_holds_and_posts_nothing(db_session: AsyncSession) -> None:
    held = {
        "suspended": await make_partner(db_session, status="suspended"),
        "kyc_incomplete": await make_partner(db_session, review="pending", bank_review="accepted"),
        "no_payout_account": await make_partner(db_session, bank_number="123456780000"),
    }
    for partner in held.values():
        await earn(db_session, partner.id, 25_000, IN_WEEK)

    summary = await run(db_session)

    assert (summary["held"], summary["pending"], summary["processed"]) == (3, 0, 0)
    for reason, partner in held.items():
        (row,) = await rows(db_session, partner.id)
        assert (row.status, row.hold_reason, row.entry_id, row.amount_paise) == (
            "held",
            reason,
            None,
            25_000,
        )
        assert await entries(db_session, row.id) == []
        assert await payable(db_session, partner.id) == 25_000
    assert await clearing(db_session) == 0 and await sent_events(db_session) == []


async def test_the_next_run_pays_held_weeks_once_fixed_and_a_new_row_takes_only_new_money(
    db_session: AsyncSession,
) -> None:
    partner = await make_partner(db_session, review="pending", bank_review="accepted")
    await earn(db_session, partner.id, 50_000, IN_WEEK)
    await run(db_session)
    await earn(db_session, partner.id, 30_000, IN_WEEK + timedelta(days=7))

    second = await run(db_session, RUN + timedelta(days=7))  # still pending review

    assert [(p.week, p.status, p.amount_paise) for p in await rows(db_session, partner.id)] == [
        ("2027-W02", "held", 50_000),
        ("2027-W03", "held", 30_000),  # not 80,000: W02's money waits in its own row
    ]
    assert second["held"] == 1 and second["retried"] == 0
    await accept_documents(db_session, partner)

    third = await run(db_session, RUN + timedelta(days=14))

    assert (third["retried"], third["processed"], third["carried"]) == (2, 2, 1)
    assert [(p.week, p.status) for p in await rows(db_session, partner.id)] == [
        ("2027-W02", "processed"),
        ("2027-W03", "processed"),
    ]
    assert await payable(db_session, partner.id) == 0
    assert await clearing(db_session) == -80_000


async def accept_documents(db: AsyncSession, partner: Partner) -> None:
    await db.execute(
        update(KycDocument)
        .where(KycDocument.application_id == partner.application_id)
        .values(review_status="accepted")
    )


async def test_a_failed_payout_is_reversed_and_waits_in_payable_for_the_next_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    partner = await make_partner(db_session)
    await earn(db_session, partner.id, 60_000, IN_WEEK)
    failing(monkeypatch)

    summary = await run(db_session)

    (row,) = await rows(db_session, partner.id)
    assert (summary["pending"], summary["refused"], summary["processed"]) == (1, 1, 0)
    assert (row.status, row.provider_ref, row.processed_at) == (
        "failed",
        f"pout_fail_{row.id.hex}",
        None,
    )
    assert await entries(db_session, row.id) == [
        ("payout", f"payout:{partner.id}:{WEEK}"),
        ("payout_reversed", f"payout_reversed:{partner.id}:{WEEK}"),
    ]
    assert await payable(db_session, partner.id) == 60_000  # back where it was
    assert await clearing(db_session) == 0
    assert [e["status"] for e in await sent_events(db_session)] == ["failed"]

    monkeypatch.undo()  # the provider pays again
    await earn(db_session, partner.id, 15_000, IN_WEEK + timedelta(days=7))
    later = await run(db_session, RUN + timedelta(days=7))

    assert (later["retried"], later["pending"], later["processed"]) == (1, 1, 2)
    old, new = await rows(db_session, partner.id)
    assert (old.status, old.attempts, old.amount_paise) == ("processed", 2, 60_000)
    assert (new.status, new.amount_paise) == ("processed", 15_000)
    assert [key for _, key in await entries(db_session, old.id)] == [
        f"payout:{partner.id}:{WEEK}",
        f"payout_reversed:{partner.id}:{WEEK}",
        f"payout:{partner.id}:{WEEK}:2",
    ]
    assert await payable(db_session, partner.id) == 0
    assert await clearing(db_session) == -75_000
    assert [e["status"] for e in await sent_events(db_session)] == [
        "failed",
        "processed",
        "processed",
    ]


async def test_a_partner_whose_run_fails_is_counted_and_the_others_are_paid(
    db_session: AsyncSession,
) -> None:
    orphan = uuid.uuid4()  # a payable with no partner record behind it
    await earn(db_session, orphan, 20_000, IN_WEEK)
    partner = await make_partner(db_session)
    await earn(db_session, partner.id, 20_000, IN_WEEK)

    summary = await run(db_session)

    assert (summary["failed"], summary["processed"]) == (1, 1)
    assert await rows(db_session, orphan) == []
    assert await payable(db_session, orphan) == 20_000


async def test_a_payout_left_pending_by_a_crash_is_sent_by_the_next_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    partner = await make_partner(db_session)
    await earn(db_session, partner.id, 20_000, IN_WEEK)

    async def crash(*_: Any, **__: Any) -> dict[str, int]:
        raise RuntimeError("the process died after the commit")

    monkeypatch.setattr(payouts, "send_pending", crash)
    crashed = await run_job(
        db_session, "weekly_payouts", settings=make_settings(), scheduled_for=RUN
    )
    monkeypatch.undo()

    assert crashed.status == "failed"
    (row,) = await rows(db_session, partner.id)
    assert row.status == "pending"  # committed before the provider was asked
    again = await run(db_session, RUN + timedelta(minutes=10))
    assert (again["existing"], again["processed"]) == (1, 1)
    await db_session.refresh(row)
    assert row.status == "processed" and len(await entries(db_session, row.id)) == 1


async def test_the_ledger_balances_and_payable_clearing_and_paid_add_up(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    people = [await make_partner(db_session) for _ in range(3)]
    people.append(await make_partner(db_session, status="suspended"))
    earned = 0
    for n, partner in enumerate(people):
        for day in range(3):
            amount = 10_000 * (n + 1) + 700 * day
            await earn(db_session, partner.id, amount, IN_WEEK + timedelta(days=7 * day))
            earned += amount
    await run(db_session)
    failing(monkeypatch)
    await run(db_session, RUN + timedelta(days=7))
    monkeypatch.undo()
    await run(db_session, RUN + timedelta(days=14))

    unbalanced = await db_session.execute(
        text(
            "SELECT e.id FROM billing.journal_entries e JOIN billing.postings p"
            " ON p.entry_id = e.id JOIN billing.ledger_accounts a ON a.id = p.account_id"
            " WHERE e.kind IN ('earning', 'payout', 'payout_reversed')"
            " GROUP BY e.id, a.currency HAVING sum(p.amount) <> 0"
        )
    )
    assert unbalanced.all() == []
    owed = sum([await payable(db_session, p.id) for p in people])
    found = await db_session.execute(select(Payout))
    every = list(found.scalars())
    paid = sum(p.amount_paise for p in every if p.status in PAYOUT_SENT)
    assert owed + paid == earned  # what was earned is either still owed or paid out
    assert -await clearing(db_session) == paid
    assert {p.status for p in every} == {"processed", "held"}


def test_the_razorpay_route_provider_is_a_configuration_error() -> None:
    with pytest.raises(ValidationError, match="razorpay_route is not built"):
        make_settings(payouts_provider="razorpay_route")
    assert payouts.provider_for(make_settings()).name == "simulated"


# --- the endpoints ---------------------------------------------------------------------------


async def person(
    api: httpx2.AsyncClient,
    google: FakeGoogle,
    db: AsyncSession,
    phone: str,
    *roles: tuple[str, uuid.UUID | None],
) -> tuple[dict[str, str], uuid.UUID]:
    user, _ = await find_or_create_user(db, phone)
    for role, scope in roles:
        db.add(
            UserRole(
                user_id=user.id,
                role=role,
                scope_type=None if scope is None else "hub",
                scope_id=scope,
            )
        )
    await db.flush()
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"]), user.id


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def test_a_partner_sees_her_own_payouts_and_statement_only(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle, monkeypatch: Any
) -> None:
    api = clients()
    kavita, kavita_id = await person(
        api, google, db_session, "+919800000701", ("packer", HUB_A), ("rider", HUB_A)
    )
    packer = await make_partner(db_session, user_id=kavita_id, kind="packer", hub_id=HUB_A)
    rider = await make_partner(db_session, user_id=kavita_id, kind="rider", hub_id=HUB_A)
    other = await make_partner(db_session, hub_id=HUB_A)
    customer, _ = await person(api, google, db_session, "+919800000702")
    for partner in (packer, rider, other):
        await earn(db_session, partner.id, 30_000, IN_WEEK)
    failing(monkeypatch)
    await run(db_session)
    monkeypatch.undo()
    await run(db_session, RUN + timedelta(days=7))

    mine = await api.get("/v1/payouts", headers=kavita)
    one = await api.get("/v1/payouts", params={"limit": 1}, headers=kavita)
    rest = await api.get(
        "/v1/payouts", params={"cursor": one.json()["next_cursor"]}, headers=kavita
    )
    month = datetime.now(IST).strftime("%Y-%m")
    statement = await api.get(
        "/v1/statements", params={"period": month, "account": "earnings"}, headers=kavita
    )

    assert mine.status_code == 200, mine.text
    assert mine.headers["Cache-Control"] == "no-store"
    items = mine.json()["items"]
    assert {p["partner_id"] for p in items} == {str(packer.id), str(rider.id)}
    assert [p["status"] for p in items] == ["processed", "processed"]
    assert items[0]["period"] == {"from": "2027-01-11", "to": "2027-01-17"}
    assert len(one.json()["items"]) == 1 and len(rest.json()["items"]) == 1
    assert rest.json()["next_cursor"] is None
    body = statement.json()
    assert statement.status_code == 200, statement.text
    assert (body["account"], body["opening_paise"], body["closing_paise"]) == ("earnings", 0, 0)
    assert (body["credits_paise"], body["debits_paise"]) == (120_000, -120_000)
    kinds = [(e["kind"], e["amount_paise"], e["ref"]["type"]) for e in body["entries"]]
    assert sorted(kinds) == sorted(
        [("earning", 30_000, "order")] * 2
        + [("payout", -30_000, "payout")] * 4
        + [("payout_reversed", 30_000, "payout")] * 2
    )
    assert body["entries"][0]["balance_after_paise"] == 0  # newest first, all paid now
    # A customer has no payouts and no earnings statement; her wallet statement is unchanged.
    assert (await api.get("/v1/payouts", headers=customer)).status_code == 403
    no_earnings = await api.get(
        "/v1/statements", params={"period": month, "account": "earnings"}, headers=customer
    )
    assert no_earnings.status_code == 403
    wallet = await api.get("/v1/statements", params={"period": month}, headers=customer)
    assert wallet.status_code == 200 and "account" not in wallet.json()
    # The partner role chosen in the app does not open the wallet statement.
    as_packer = {**kavita, "X-Active-Role": f"packer:{HUB_A}"}
    chosen = await api.get("/v1/statements", params={"period": month}, headers=as_packer)
    assert chosen.status_code == 403
    assert (await api.get("/v1/admin/payouts", headers=kavita)).status_code == 403


async def test_the_earnings_week_shows_its_payouts(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth, user_id = await person(api, google, db_session, "+919800000703", ("packer", HUB_A))
    partner = await make_partner(db_session, user_id=user_id, kind="packer", hub_id=HUB_A)
    now = datetime.now(UTC)  # M8 refuses a week after the current one
    last = payouts.week_before(now)
    await earn(db_session, partner.id, 30_000, week_bounds(last)[2] + timedelta(days=2))
    await run(db_session, now)

    week = await billing_api.partner_week(db_session, [partner.id], last)
    earnings = await api.get("/v1/earnings", params={"week": last}, headers=auth)

    (payout,) = week.payouts
    assert (payout.status, payout.amount_paise, payout.week) == ("processed", 30_000, last)
    assert earnings.status_code == 200, earnings.text
    (shown,) = earnings.json()["payouts"]
    assert (shown["kind"], shown["id"], shown["status"]) == ("packer", str(payout.id), "processed")
    assert (await billing_api.partner_week(db_session, [partner.id], "2027-W03")).payouts == []


async def test_staff_read_payouts_by_week_status_and_hub_and_a_hub_manager_only_hers(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    admin, _ = await person(api, google, db_session, "+919800000704", ("admin", None))
    manager, _ = await person(api, google, db_session, "+919800000705", ("hub_manager", HUB_A))
    in_a = await make_partner(db_session, hub_id=HUB_A, name="Lata tai", kind="kitchen")
    held_a = await make_partner(db_session, hub_id=HUB_A, status="suspended")
    in_b = await make_partner(db_session, hub_id=HUB_B)
    for partner in (in_a, held_a, in_b):
        await earn(db_session, partner.id, 20_000, IN_WEEK)
    # A refused request rolls the shared session back, which expires these objects: keep ids.
    a, held_id, b = str(in_a.id), str(held_a.id), str(in_b.id)
    await run(db_session)

    everything = await api.get("/v1/admin/payouts", params={"week": WEEK}, headers=admin)
    held = await api.get("/v1/admin/payouts", params={"status": "held"}, headers=admin)
    hub_b = await api.get("/v1/admin/payouts", params={"hub_id": str(HUB_B)}, headers=admin)
    other_week = await api.get("/v1/admin/payouts", params={"week": "2027-W03"}, headers=admin)
    hers = await api.get("/v1/admin/payouts", headers=manager)
    hers_a = await api.get("/v1/admin/payouts", params={"hub_id": str(HUB_A)}, headers=manager)
    not_hers = await api.get("/v1/admin/payouts", params={"hub_id": str(HUB_B)}, headers=manager)

    assert everything.status_code == 200, everything.text
    by_partner = {p["partner_id"]: p for p in everything.json()["items"]}
    assert set(by_partner) == {a, held_id, b}
    assert by_partner[a]["partner"] == {"display_name": "Lata tai", "kind": "kitchen"}
    assert by_partner[held_id]["hold_reason"] == "suspended"
    assert [p["partner_id"] for p in held.json()["items"]] == [held_id]
    assert [p["partner_id"] for p in hub_b.json()["items"]] == [b]
    assert other_week.json()["items"] == []
    assert {p["partner_id"] for p in hers.json()["items"]} == {a, held_id}
    assert hers_a.json() == hers.json()
    assert not_hers.status_code == 403
    row = by_partner[held_id]["id"]
    refused = await api.post(f"/v1/admin/payouts/{row}/retry", json={}, headers=manager | key())
    assert refused.status_code == 403  # read-only
    for bad in ("2027-02", "2027-W54", "1999-W01", "2027-W00"):
        answer = await api.get("/v1/admin/payouts", params={"week": bad}, headers=admin)
        assert (answer.status_code, answer.json()["code"]) == (422, "billing.week_invalid"), bad


async def test_an_admin_retries_a_held_payout_once_its_reason_is_fixed(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    admin, admin_id = await person(api, google, db_session, "+919800000706", ("admin", None))
    partner = await make_partner(db_session, status="suspended")
    await earn(db_session, partner.id, 45_000, IN_WEEK)
    await run(db_session)
    (row,) = await rows(db_session, partner.id)
    partner_id, row_id = partner.id, row.id  # a refused request expires the objects
    url = f"/v1/admin/payouts/{row_id}/retry"

    too_soon = await api.post(url, json={}, headers=admin | key())
    assert (too_soon.status_code, too_soon.json()["code"]) == (409, "billing.payout_still_held")
    assert too_soon.json()["hold_reason"] == "suspended"
    await db_session.execute(
        update(Partner)
        .where(Partner.id == partner_id)
        .values(status="active", suspended_at=None, suspend_reason_code=None)
    )
    once = key()
    retried = await api.post(url, json={}, headers=admin | once)
    replay = await api.post(url, json={}, headers=admin | once)
    again = await api.post(url, json={}, headers=admin | key())
    unknown = f"/v1/admin/payouts/{uuid.uuid4()}/retry"
    missing = await api.post(unknown, json={}, headers=admin | key())
    no_key = await api.post(url, json={}, headers=admin)

    assert retried.status_code == 200, retried.text
    assert (retried.json()["status"], retried.json()["hold_reason"]) == ("pending", None)
    assert replay.json() == retried.json()
    assert (again.status_code, again.json()["code"]) == (409, "billing.payout_not_retryable")
    assert again.json()["payout_status"] == "processed"
    assert (missing.status_code, missing.json()["code"]) == (404, "billing.payout_not_found")
    assert no_key.status_code in (400, 422)
    row = await db_session.get(Payout, row_id, populate_existing=True)
    assert row is not None
    assert (row.status, row.attempts) == ("processed", 1)  # the provider step ran after commit
    assert await entries(db_session, row_id) == [("payout", f"payout:{partner_id}:{WEEK}")]
    assert await payable(db_session, partner_id) == 0
    audits = await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "billing.payout_retried")
    )
    (audit,) = audits.scalars()
    assert (audit.actor_user_id, audit.entity_id) == (admin_id, str(row_id))
    assert audit.before == {"status": "held", "hold_reason": "suspended"}
    assert [e["status"] for e in await sent_events(db_session)] == ["processed"]


async def test_an_admin_retries_a_failed_payout_with_a_new_attempt(
    db_session: AsyncSession,
    clients: ClientFactory,
    google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    admin, _ = await person(api, google, db_session, "+919800000707", ("admin", None))
    partner = await make_partner(db_session)
    await earn(db_session, partner.id, 45_000, IN_WEEK)
    failing(monkeypatch)
    await run(db_session)
    monkeypatch.undo()
    (row,) = await rows(db_session, partner.id)

    retried = await api.post(f"/v1/admin/payouts/{row.id}/retry", json={}, headers=admin | key())

    assert retried.status_code == 200, retried.text
    await db_session.refresh(row)
    assert (row.status, row.attempts, row.provider_ref) == (
        "processed",
        2,
        f"pout_sim_{row.id.hex}",
    )
    assert [kind for kind, _ in await entries(db_session, row.id)] == [
        "payout",
        "payout_reversed",
        "payout",
    ]
    assert await payable(db_session, partner.id) == 0
    assert await clearing(db_session) == -45_000
