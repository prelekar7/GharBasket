"""Account deletion (M1 §3.5): the request, the grace period, cancelling and the purge."""

import time
import uuid
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.models import Payment, PaymentWebhook, Posting
from gharbasket.modules.billing.wallet import credit_topup, debit_for_order
from gharbasket.modules.hub_ops.models import Hub, PackingTask
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity import deletion
from gharbasket.modules.identity import jobs as identity_jobs
from gharbasket.modules.identity.models import (
    DataExport,
    Deletion,
    Household,
    LoginSession,
    User,
    UserRole,
)
from gharbasket.modules.intel_gateway.models import FeatureSnapshot, Prediction
from gharbasket.modules.notifications.models import Delivery, Message, Preference, QuietHours
from gharbasket.modules.orders.models import Order, OrderIssue, Slot
from gharbasket.modules.subscriptions import jobs as subscriptions_jobs
from gharbasket.modules.subscriptions.models import Subscription, WeeklyCycle
from gharbasket.platform import outbox
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, IdempotencyKey, OutboxEvent, Upload
from gharbasket.tools.seed import seed_hubs, seed_societies

from ..conftest import make_settings
from .conftest import ANDROID, ClientFactory, bearer
from .fake_firebase import FakeGoogle
from .onboarding import onboard
from .test_data_export import FakeS3, activity

pytestmark = [pytest.mark.anyio, pytest.mark.services]

SETTINGS = make_settings(
    s3_endpoint_url="http://seaweedfs:8333",
    s3_access_key="test-access",
    s3_secret_key="test-storage-secret",
)
IST_OFFSET = timedelta(hours=5, minutes=30)
# What the purge keeps on purpose (spec §3.5 matrix): the tombstone, and the audit log whose rows
# carry the actor's id and no personal data.
KEPT = {("identity", "deletions"), ("platform", "audit_log")}


@pytest.fixture(autouse=True)
async def societies(db_session: AsyncSession) -> None:
    await seed_societies(db_session)


@pytest.fixture
def s3() -> FakeS3:
    return FakeS3()


@pytest.fixture
def store(s3: FakeS3) -> ObjectStore:
    found = create_store(SETTINGS)
    assert found is not None
    found.client = s3
    return found


def phone(n: int) -> str:
    return f"+91980001{n:04d}"


async def exchange(api: Any, google: FakeGoogle, n: int = 1, **body: Any) -> Any:
    claims: dict[str, Any] = {"phone_number": phone(n), "sub": f"del-{n}"}
    if "auth_time" in body:
        claims["auth_time"] = body.pop("auth_time")
    return await api.post(
        "/v1/auth/exchange",
        json={"firebase_id_token": google.id_token(**claims), "device": ANDROID, **body},
    )


async def signed_in(api: Any, google: FakeGoogle, n: int = 1, **body: Any) -> dict[str, Any]:
    answer = await exchange(api, google, n, **body)
    assert answer.status_code == 200, answer.text
    return answer.json()


async def customer(api: Any, google: FakeGoogle, n: int = 1) -> tuple[dict[str, str], uuid.UUID]:
    """A signed-in customer with a household."""
    body = await signed_in(api, google, n)
    headers = bearer(body["access_token"])
    assert (await onboard(api, headers)).status_code == 201
    return headers, uuid.UUID(body["user"]["id"])


async def household_id(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    found = await deletion.household_id_of(db, user_id)
    assert found is not None
    return found


async def events_of(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.scalars(select(OutboxEvent.payload).where(OutboxEvent.event_type == event_type))
    return list(rows)


def ist_day(day: date) -> datetime:
    """03:33 IST on `day`, when the purge runs."""
    return datetime(day.year, day.month, day.day, 3, 33, tzinfo=UTC) - IST_OFFSET


async def purge(db: AsyncSession, day: date) -> dict[str, Any]:
    run = await run_job(db, "purge_deleted_users", settings=SETTINGS, scheduled_for=ist_day(day))
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


async def ledger_sum(db: AsyncSession) -> int:
    return (await db.execute(select(func.coalesce(func.sum(Posting.amount), 0)))).scalar_one()


async def rows_naming(db: AsyncSession, ids: list[uuid.UUID]) -> dict[str, int]:
    """Every uuid column of every module's schema that still holds one of `ids`."""
    columns = await db.execute(
        text(
            "SELECT table_schema, table_name, column_name FROM information_schema.columns "
            "WHERE data_type = 'uuid' AND table_schema NOT IN "
            "('pg_catalog', 'information_schema', 'public', 'tiger', 'topology')"
        )
    )
    found: dict[str, int] = {}
    for schema, table, column in columns.all():
        if (schema, table) in KEPT:
            continue
        count = await db.scalar(
            text(f'SELECT count(*) FROM "{schema}"."{table}" WHERE "{column}" = ANY(:ids)'),
            {"ids": ids},
        )
        if count:
            found[f"{schema}.{table}.{column}"] = count
    return found


# --- the request ------------------------------------------------------------------------------


async def test_deleting_signs_out_every_device_and_schedules_the_purge(
    clients: ClientFactory,
    google: FakeGoogle,
    valkey: Redis,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api = clients(valkey=valkey, storage=store)
    headers, user_id = await customer(api, google)
    tablet = await signed_in(api, google)
    assert (await api.post("/v1/me/data-export", headers=headers)).status_code == 202
    built = await run_job(db_session, "build_data_export", settings=SETTINGS, storage=store)
    assert built.status == "ok" and built.summary == {"ready": 1, "retry": 0, "failed": 0}
    export_key = (await db_session.scalars(select(DataExport.object_key))).one()
    assert export_key is not None and export_key in s3.objects

    answer = await api.delete("/v1/me", headers=headers)
    assert answer.status_code == 202, answer.text
    body = answer.json()
    today = datetime.now(deletion.IST).date()
    assert body == {
        "status": "scheduled",
        "requested_at": body["requested_at"],
        "purge_on": (today + timedelta(days=30)).isoformat(),
        "wallet_refund": None,
    }

    # Every device is out at once: this one, the tablet's access and refresh tokens.
    assert (await api.get("/v1/me", headers=headers)).status_code == 401
    assert (await api.get("/v1/me", headers=bearer(tablet["access_token"]))).status_code == 401
    refreshed = await api.post("/v1/auth/refresh", json={"refresh_token": tablet["refresh_token"]})
    assert refreshed.status_code == 401
    reasons = await db_session.scalars(
        select(LoginSession.revoke_reason).where(LoginSession.user_id == user_id)
    )
    assert set(reasons) == {"account_deleted"}

    user = await db_session.get(User, user_id, populate_existing=True)
    assert user is not None and user.status == "deleting"
    assert not await identity_api.is_active(db_session, user_id)
    scheduled = await db_session.get(Deletion, user_id)
    assert scheduled is not None and scheduled.purge_on == today + timedelta(days=30)
    # The export's row is gone and its file is due for tonight's sweep.
    assert (await db_session.scalar(select(func.count()).select_from(DataExport))) == 0
    upload = await db_session.get(Upload, export_key, populate_existing=True)
    assert upload is not None and upload.delete_after is not None
    assert upload.delete_after <= datetime.now(UTC)

    assert await events_of(db_session, "identity.deletion_requested") == [
        {
            "user_id": str(user_id),
            "household_id": str(await household_id(db_session, user_id)),
            "purge_on": body["purge_on"],
        }
    ]
    audited = await db_session.scalars(
        select(AuditEntry).where(AuditEntry.action == "identity.deletion_requested")
    )
    entry = audited.one()
    assert entry.entity_id == str(user_id) and entry.before is None and entry.after is None


async def test_a_retried_delete_answers_the_same_body_without_valkey(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()  # no deny-list: the database still knows the session ended
    headers, _ = await customer(api, google)
    first = await api.delete("/v1/me", headers=headers)
    again = await api.delete("/v1/me", headers=headers)
    assert first.status_code == again.status_code == 202
    assert again.json() == first.json()
    assert (await api.get("/v1/me", headers=headers)).status_code == 401


async def test_staff_must_lose_their_roles_first(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, user_id = await customer(api, google)
    db_session.add_all(
        [
            UserRole(user_id=user_id, role="hub_manager", scope_type="hub", scope_id=uuid.uuid4()),
            UserRole(user_id=user_id, role="rider", scope_type="hub", scope_id=uuid.uuid4()),
        ]
    )
    await db_session.commit()

    answer = await api.delete("/v1/me", headers=headers)
    assert answer.status_code == 409
    assert answer.json()["code"] == "identity.staff_account"
    assert answer.json()["roles"] == ["hub_manager"]
    assert await identity_api.is_active(db_session, user_id)
    assert await db_session.get(Deletion, user_id) is None


async def test_an_old_otp_is_refused_and_a_refresh_does_not_renew_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    signed = await signed_in(api, google, auth_time=int(time.time()) - 301)
    headers = bearer(signed["access_token"])
    answer = await api.delete("/v1/me", headers=headers)
    assert answer.status_code == 403
    assert answer.json()["code"] == "identity.fresh_otp_required"
    assert answer.json()["max_age_s"] == 300

    login = (await db_session.scalars(select(LoginSession))).one()
    before = login.auth_time
    refreshed = await api.post("/v1/auth/refresh", json={"refresh_token": signed["refresh_token"]})
    assert refreshed.status_code == 200
    await db_session.refresh(login)
    assert login.auth_time == before
    retried = await api.delete("/v1/me", headers=bearer(refreshed.json()["access_token"]))
    assert retried.status_code == 403

    # A new OTP is a new sign-in: that one may delete.
    fresh = await signed_in(api, google)
    assert (await api.delete("/v1/me", headers=bearer(fresh["access_token"]))).status_code == 202


# --- cancelling -------------------------------------------------------------------------------


async def test_signing_in_during_the_grace_asks_then_keeps_the_account(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, user_id = await customer(api, google)
    scheduled = (await api.delete("/v1/me", headers=headers)).json()
    sessions = await db_session.scalar(select(func.count()).select_from(LoginSession))

    asked = await exchange(api, google)
    assert asked.status_code == 409
    assert asked.json()["code"] == "identity.deletion_pending"
    assert asked.json()["purge_on"] == scheduled["purge_on"]
    # Nothing was signed in.
    assert await db_session.scalar(select(func.count()).select_from(LoginSession)) == sessions
    assert not await identity_api.is_active(db_session, user_id)

    kept = await signed_in(api, google, keep_account=True)
    assert kept["deletion_cancelled"] is True and kept["user"]["id"] == str(user_id)
    assert (await api.get("/v1/me", headers=bearer(kept["access_token"]))).status_code == 200
    assert await identity_api.is_active(db_session, user_id)
    row = await db_session.get(Deletion, user_id, populate_existing=True)
    assert row is not None and row.cancelled_at is not None
    assert await events_of(db_session, "identity.deletion_cancelled") == [{"user_id": str(user_id)}]
    again = await signed_in(api, google)
    assert again["deletion_cancelled"] is False

    # The cancelled deletion is never purged; asking again reuses the row.
    assert (await purge(db_session, date.today() + timedelta(days=60)))["purged"] == 0
    assert (await api.delete("/v1/me", headers=bearer(again["access_token"]))).status_code == 202
    row = await db_session.get(Deletion, user_id, populate_existing=True)
    assert row is not None and row.cancelled_at is None


# --- the purge --------------------------------------------------------------------------------


async def test_the_purge_erases_the_account_and_keeps_the_ledger(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api = clients()
    headers, user_id = await customer(api, google)
    home = await household_id(db_session, user_id)
    # Money moved and an order was delivered: both stay, under an id that leads to nobody.
    order_id = uuid.uuid4()
    await credit_topup(db_session, user_id, 20_000, payment_ref=f"p{user_id}", topup_id=order_id)
    await debit_for_order(db_session, user_id, order_id, 20_000, date(2026, 10, 13))
    photo = f"order_issue_photo/{user_id}/{uuid.uuid4()}.jpg"
    s3.objects[photo] = ("image/jpeg", b"jpeg")
    db_session.add_all(
        [
            Upload(
                key=photo,
                user_id=user_id,
                purpose="order_issue_photo",
                content_type="image/jpeg",
                size_bytes=4,
            ),
            IdempotencyKey(scope=str(user_id), key="k-1", request_hash="h"),
        ]
    )
    await db_session.commit()
    assert await ledger_sum(db_session) == 0

    purge_on = date.fromisoformat((await api.delete("/v1/me", headers=headers)).json()["purge_on"])
    early = await purge(db_session, purge_on - timedelta(days=1))
    assert early == {"purged": 0, "blocked": {}, "overdue": [], "failed": {}}
    assert await db_session.get(User, user_id) is not None

    assert (await purge(db_session, purge_on))["purged"] == 1
    tombstone = await db_session.get(Deletion, user_id, populate_existing=True)
    assert tombstone is not None and tombstone.purged_at is not None
    assert tombstone.blocked_reasons == []
    assert tombstone.counts is not None
    assert tombstone.counts["identity"] == {
        "refresh_tokens": 1,
        "sessions": 1,
        "user_roles": 1,
        "consents": 3,
        "data_exports": 0,
        "household_members": 2,
        "addresses": 1,
        "households": 1,
        "users": 1,
    }
    assert tombstone.counts["platform"] == {"idempotency_keys": 2, "uploads": 1}
    assert await events_of(db_session, "identity.user_deleted") == [
        {"user_id": str(user_id), "household_id": str(home)}
    ]

    # Tonight's sweep removes the user's files; then no row anywhere names the user or the
    # household, except the ledger account that keeps the books.
    swept = await run_job(db_session, "sweep_uploads", settings=SETTINGS, storage=store)
    assert swept.status == "ok" and photo not in s3.objects
    assert await rows_naming(db_session, [user_id, home]) == {"billing.ledger_accounts.owner_id": 1}
    assert await ledger_sum(db_session) == 0
    phone_text = await db_session.scalar(
        text(
            "SELECT (SELECT count(*) FROM platform.outbox WHERE payload::text LIKE :p) "
            "+ (SELECT count(*) FROM platform.audit_log WHERE "
            "coalesce(before::text, '') || coalesce(after::text, '') LIKE :p)"
        ),
        {"p": f"%{phone(1)[3:]}%"},
    )
    assert phone_text == 0

    # The old tokens lead nowhere; the same phone signs up as somebody new.
    assert (await api.get("/v1/me", headers=headers)).status_code == 401
    again = await signed_in(api, google)
    assert again["is_new_user"] is True and again["user"]["id"] != str(user_id)

    # A rerun touches nothing.
    assert await purge(db_session, purge_on + timedelta(days=1)) == early


async def test_the_purge_waits_for_orders_issues_and_the_wallet(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, user_id = await customer(api, google)
    home = await household_id(db_session, user_id)
    household = await db_session.get(Household, home)
    assert household is not None
    hub_id = uuid.uuid4()
    slot = Slot(hub_id=hub_id, label="Morning", start_time=clock(7), end_time=clock(9), capacity=5)
    db_session.add(slot)
    await db_session.flush()
    order = Order(
        household_id=home,
        hub_id=hub_id,
        society_id=household.society_id,
        kind="box",
        delivery_date=date(2026, 10, 13),
        slot_id=slot.id,
        cutoff_at=datetime(2026, 10, 12, 15, 30, tzinfo=UTC),
        status="dispatched",
        subtotal_paise=12_000,
        total_paise=12_000,
    )
    db_session.add(order)
    await db_session.flush()
    issue = OrderIssue(
        order_id=order.id,
        household_id=home,
        hub_id=order.hub_id,
        kind="missing",
        status="open",
        open_reason="photo_needed",
        photo_keys=[],
        amount_paise=3_000,
        created_by=user_id,
    )
    db_session.add(issue)
    await credit_topup(db_session, user_id, 5_000, payment_ref=f"w{user_id}", topup_id=uuid.uuid4())
    await db_session.commit()
    purge_on = date.fromisoformat((await api.delete("/v1/me", headers=headers)).json()["purge_on"])

    waiting = await purge(db_session, purge_on)
    assert waiting["purged"] == 0
    assert waiting["blocked"] == {str(user_id): ["open_orders", "open_issues", "wallet_not_closed"]}
    assert waiting["overdue"] == []
    row = await db_session.get(Deletion, user_id, populate_existing=True)
    assert row is not None and row.blocked_reasons == waiting["blocked"][str(user_id)]
    assert row.purged_at is None and await db_session.get(User, user_id) is not None

    late = await purge(db_session, purge_on + timedelta(days=16))
    assert late["overdue"] == [str(user_id)]

    order.status, issue.status, issue.open_reason = "delivered", "rejected", None
    issue.resolved_by, issue.resolved_at = "hub", datetime.now(UTC)
    await debit_for_order(db_session, user_id, order.id, 5_000, order.delivery_date)
    await db_session.commit()
    assert (await purge(db_session, purge_on + timedelta(days=17)))["purged"] == 1


async def test_one_failing_eraser_does_not_stop_the_others(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    first, unlucky = await customer(api, google, 1)
    second, lucky = await customer(api, google, 2)
    purge_on = date.fromisoformat((await api.delete("/v1/me", headers=first)).json()["purge_on"])
    assert (await api.delete("/v1/me", headers=second)).status_code == 202

    async def flaky(
        session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
    ) -> dict[str, int]:
        if user_id == unlucky:
            raise RuntimeError("storage is down")
        return {}

    erasers = [("flaky", flaky), *identity_jobs.ERASERS]
    monkeypatch.setattr(identity_jobs, "ERASERS", erasers)
    summary = await purge(db_session, purge_on)
    assert summary["purged"] == 1 and summary["failed"] == {str(unlucky): "RuntimeError"}
    assert await db_session.get(User, lucky) is None
    # Nothing of the failed purge stayed: the account is whole and still scheduled.
    kept = await db_session.get(User, unlucky, populate_existing=True)
    assert kept is not None and kept.status == "deleting"
    assert await deletion.household_id_of(db_session, unlucky) is not None
    row = await db_session.get(Deletion, unlucky, populate_existing=True)
    assert row is not None and row.purged_at is None and row.counts is None

    monkeypatch.setattr(identity_jobs, "ERASERS", erasers[1:])
    assert (await purge(db_session, purge_on))["purged"] == 1
    assert await db_session.get(User, unlucky) is None


# --- every module's eraser --------------------------------------------------------------------


async def test_every_module_erases_its_part(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """A customer with a week of history in every module that holds personal data: after the
    purge only the records the matrix keeps still carry the ids, and they hold nothing personal."""
    api = clients()
    headers, user_id = await customer(api, google)
    home = await household_id(db_session, user_id)
    order_id = await activity(db_session, user_id)
    await debit_for_order(db_session, user_id, order_id, 50_000, date(2026, 10, 13))
    await seed_hubs(db_session)
    hub_id = (await db_session.scalars(select(Hub.id).limit(1))).one()
    payment = (await db_session.scalars(select(Payment).where(Payment.user_id == user_id))).one()
    entity = {"id": payment.provider_payment_id, "order_id": payment.provider_order_id}
    message = Message(
        user_id=user_id,
        event_id=uuid.uuid4(),
        event_type="orders.delivered",
        kind="delivered",
        category="delivery",
        params={},
    )
    db_session.add_all(
        [
            message,
            Preference(user_id=user_id, category="offers", push=False, whatsapp=False),
            QuietHours(user_id=user_id, start_time=clock(22), end_time=clock(7)),
            Prediction(
                kind="weekly_plan",
                subject_type="household",
                subject_id=home,
                for_date=date(2026, 10, 12),
                source="fallback",
                payload={},
                model_name="rules",
            ),
            FeatureSnapshot(
                subject_type="household", subject_id=home, as_of=datetime.now(UTC), features={}
            ),
            PackingTask(
                hub_id=hub_id,
                order_id=order_id,
                order_kind="box",
                delivery_date=date(2026, 10, 13),
                slot_id=uuid.uuid4(),
                society_id=uuid.uuid4(),
                tower_id="a",
                flat="a-1203",
                seq=1,
            ),
            PaymentWebhook(
                provider="razorpay",
                event_id=f"evt_{user_id.hex[:10]}",
                event="payment.captured",
                payload={
                    "payment": {
                        "entity": {**entity, "contact": "+919800010001", "vpa": "priya@upi"}
                    }
                },
            ),
        ]
    )
    await db_session.flush()
    db_session.add(Delivery(message_id=message.id, channel="push", status="sent"))
    await db_session.commit()
    assert await ledger_sum(db_session) == 0

    purge_on = date.fromisoformat((await api.delete("/v1/me", headers=headers)).json()["purge_on"])
    assert (await purge(db_session, purge_on))["purged"] == 1

    counts = (await db_session.get(Deletion, user_id, populate_existing=True)).counts  # type: ignore[union-attr]
    assert counts is not None
    assert set(counts) == {module for module, _ in identity_jobs.ERASERS}
    assert counts["notifications"] == {
        "deliveries": 1,
        "messages": 1,
        "preferences": 1,
        "quiet_hours": 1,
    }
    assert counts["intel_gateway"] == {"predictions": 1, "feature_snapshots": 1}
    assert counts["hub_ops"] == {"packing_tasks": 1, "packing_events": 0}
    assert counts["subscriptions"] == {
        "edit_feedback": 0,
        "pantry_feedback": 0,
        "box_lines": 1,
        "weekly_cycles": 1,
        "budget_locks": 0,
        "subscriptions": 1,
    }
    assert counts["orders"] == {"order_issues": 1, "order_events": 0}
    assert counts["billing"] == {"payment_webhooks": 1}

    # Kept on purpose (the matrix): orders and their issues, the payment, the wallet account.
    assert await rows_naming(db_session, [user_id, home]) == {
        "billing.ledger_accounts.owner_id": 1,
        "billing.payments.user_id": 1,
        "orders.orders.household_id": 1,
        "orders.order_issues.household_id": 1,
        "orders.order_issues.created_by": 1,
    }
    issue = (
        await db_session.scalars(select(OrderIssue).where(OrderIssue.household_id == home))
    ).one()
    await db_session.refresh(issue)
    assert issue.photo_keys == [] and issue.note is None and issue.credit_paise == 3_000
    task = (
        await db_session.scalars(select(PackingTask).where(PackingTask.order_id == order_id))
    ).one()
    await db_session.refresh(task)
    assert task.flat is None and task.tower_id is None
    hook = (await db_session.scalars(select(PaymentWebhook))).one()
    await db_session.refresh(hook)
    assert hook.payload["payment"]["entity"] == {**entity, "contact": "redacted", "vpa": "redacted"}
    assert await ledger_sum(db_session) == 0


async def test_the_request_ends_the_subscription_and_skips_unpaid_weeks(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, user_id = await customer(api, google)
    home = await household_id(db_session, user_id)
    await activity(db_session, user_id)
    past = (
        await db_session.scalars(select(WeeklyCycle).where(WeeklyCycle.household_id == home))
    ).one()
    past.cutoff_at = datetime.now(UTC) - timedelta(hours=1)  # an approved week past its cut-off
    subscription = (
        await db_session.scalars(select(Subscription).where(Subscription.household_id == home))
    ).one()
    later = datetime.now(UTC) + timedelta(days=3)
    week = WeeklyCycle(
        household_id=home,
        hub_id=uuid.uuid4(),
        society_id=uuid.uuid4(),
        subscription_id=subscription.id,
        week_start=date(2030, 1, 7),
        delivery_date=date(2030, 1, 8),
        slot_id=uuid.uuid4(),
        cutoff_at=later,
        plan_source="fallback",
        status="awaiting_approval",
    )
    db_session.add(week)
    await db_session.commit()

    assert (await api.delete("/v1/me", headers=headers)).status_code == 202
    await outbox.relay(db_session)
    for row in (subscription, week):
        await db_session.refresh(row)
    assert subscription.status == "ended"
    assert week.status == "skipped"
    statuses = await db_session.scalars(
        select(WeeklyCycle.status).where(WeeklyCycle.household_id == home)
    )
    assert sorted(statuses) == ["approved", "skipped"]  # the past cut-off is the cut-off's job

    # A redelivered event, or one without a household, changes nothing.
    empty = OutboxEvent(event_type="identity.deletion_requested", payload={"household_id": None})
    await subscriptions_jobs.on_deletion_requested(db_session, empty)
    await subscriptions_jobs.on_deletion_requested(
        db_session, OutboxEvent(event_type="x.y", payload={"household_id": str(home)})
    )
    await db_session.refresh(week)
    assert week.status == "skipped" and week.version == 2
