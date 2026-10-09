"""Download my data (M1 §3.3): request, build from each module's section, poll and download."""

import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.exceptions import ClientError
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.models import Payment
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.identity.models import Consent, DataExport, Household, User
from gharbasket.modules.orders.models import (
    Order,
    OrderEvent,
    OrderIssue,
    OrderIssueItem,
    OrderLine,
    Slot,
)
from gharbasket.modules.subscriptions.models import BoxLine, Plan, Subscription, WeeklyCycle
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, OutboxEvent, Upload
from gharbasket.tools.seed import seed_societies

from ..conftest import make_settings
from .conftest import ClientFactory, bearer
from .fake_firebase import FakeGoogle
from .onboarding import onboard

pytestmark = [pytest.mark.anyio, pytest.mark.services]

SETTINGS = make_settings(
    s3_endpoint_url="http://seaweedfs:8333",
    s3_access_key="test-access",
    s3_secret_key="test-storage-secret",
)
EXPORT = "/v1/me/data-export"


class FakeS3:
    """Just enough of a boto3 client for put, head and delete: objects in a dict."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[str, bytes]] = {}
        self.fail_put = False

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> dict[str, Any]:
        if self.fail_put:
            raise ClientError({"Error": {"Code": "500"}}, "PutObject")
        self.objects[Key] = (ContentType, Body)
        return {}

    def delete_objects(self, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:
        for item in Delete["Objects"]:
            self.objects.pop(item["Key"], None)
        return {}

    def file(self, key: str) -> dict[str, Any]:
        content_type, body = self.objects[key]
        assert content_type == "application/json"
        return json.loads(body)


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
    found.client = s3  # real boto3 signs the links (offline); the fake keeps the objects
    return found


async def signed_in(
    clients: ClientFactory, google: FakeGoogle, store: ObjectStore, n: int = 1, **kwargs: Any
) -> tuple[Any, dict[str, str], uuid.UUID]:
    api = clients(storage=store, **kwargs)
    answer = await api.post(
        "/v1/auth/exchange",
        json={
            "firebase_id_token": google.id_token(phone_number=f"+91980000{n:04d}", sub=f"u{n}"),
            "device": {"platform": "android", "push_token": f"push-secret-{n}"},
        },
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()
    return api, bearer(body["access_token"]), uuid.UUID(body["user"]["id"])


async def activity(db: AsyncSession, user_id: uuid.UUID) -> uuid.UUID:
    """A subscription with one cycle, one delivered order with a credited issue and a top-up;
    returns the order id."""
    household = (
        await db.execute(select(Household).where(Household.owner_user_id == user_id))
    ).scalar_one()
    hub_id = household.hub_id or uuid.uuid4()
    plan = (await db.execute(select(Plan).where(Plan.code == "autopilot"))).scalar_one()
    subscription = Subscription(household_id=household.id, plan_id=plan.id, payment_mode="wallet")
    slot = Slot(
        hub_id=hub_id,
        label=f"Morning {user_id.hex[-6:]}",
        start_time=time(7),
        end_time=time(9),
        capacity=50,
    )
    db.add_all([subscription, slot])
    await db.flush()
    day = date(2026, 10, 13)
    cutoff = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)
    cycle = WeeklyCycle(
        household_id=household.id,
        hub_id=hub_id,
        society_id=household.society_id,
        subscription_id=subscription.id,
        week_start=date(2026, 10, 12),
        delivery_date=day,
        slot_id=slot.id,
        cutoff_at=cutoff,
        plan_source="fallback",
        plan_prediction_id=uuid.uuid4(),
        status="approved",
    )
    order = Order(
        household_id=household.id,
        hub_id=hub_id,
        society_id=household.society_id,
        kind="box",
        delivery_date=day,
        slot_id=slot.id,
        cutoff_at=cutoff,
        status="delivered",
        subtotal_paise=12_000,
        total_paise=12_000,
    )
    db.add_all([cycle, order])
    await db.flush()
    product = uuid.uuid4()
    line = {"product_id": product, "qty": 2, "step_qty": 500, "step_unit": "g"}
    db.add_all(
        [
            BoxLine(
                cycle_id=cycle.id,
                **line,
                unit_price_paise=6_000,
                reason_code="meal_plan",
                source="rule",
            ),
            OrderLine(order_id=order.id, kind="box", unit_price_paise=6_000, **line),
            OrderEvent(
                order_id=order.id, to_status="delivered", actor="rider", actor_user_id=uuid.uuid4()
            ),
            Payment(
                user_id=user_id,
                provider="razorpay",
                amount_paise=50_000,
                status="paid",
                provider_order_id=f"order_{user_id.hex[:12]}",
                provider_payment_id=f"pay_{user_id.hex[:12]}",
                expires_at=cutoff,
            ),
        ]
    )
    issue = OrderIssue(
        order_id=order.id,
        household_id=household.id,
        hub_id=hub_id,
        kind="missing",
        status="credited",
        photo_keys=[f"order_issue/{user_id}/{uuid.uuid4()}.jpg"],
        amount_paise=3_000,
        credit_paise=3_000,
        resolved_by="auto",
        resolved_at=cutoff + timedelta(days=1),
        created_by=user_id,
    )
    db.add(issue)
    await db.flush()
    db.add(OrderIssueItem(issue_id=issue.id, product_id=product, qty=1, unit_price_paise=3_000))
    await credit_topup(db, user_id, 50_000, payment_ref=f"pay_{user_id.hex}", topup_id=uuid.uuid4())
    await db.flush()
    return order.id


async def build(db: AsyncSession, store: ObjectStore, at: datetime | None = None) -> Any:
    return await run_job(
        db, "build_data_export", settings=SETTINGS, storage=store, scheduled_for=at
    )


# --- request ----------------------------------------------------------------------------------


async def test_asking_twice_the_same_day_answers_the_same_export(
    clients: ClientFactory, google: FakeGoogle, store: ObjectStore, db_session: AsyncSession
) -> None:
    api, headers, user_id = await signed_in(clients, google, store)
    first = await api.post(EXPORT, headers=headers)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["status"] == "queued" and body["ready_within_hours"] == 24
    assert body["download"] is None and body["ready_at"] is None and body["expires_at"] is None

    again = await api.post(EXPORT, headers=headers)
    assert again.status_code == 202 and again.json()["id"] == body["id"]
    assert len((await db_session.execute(select(DataExport))).all()) == 1
    published = (
        (
            await db_session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.event_type == "identity.data_export_requested"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [event.payload for event in published] == [
        {"export_id": body["id"], "user_id": str(user_id)}
    ]
    audited = await db_session.execute(
        select(AuditEntry.action).where(AuditEntry.entity_id == body["id"])
    )
    assert audited.scalars().all() == ["identity.data_export_requested"] * 2


async def test_auth_and_the_first_get(
    clients: ClientFactory, google: FakeGoogle, store: ObjectStore
) -> None:
    api, headers, _ = await signed_in(clients, google, store)
    assert (await api.post(EXPORT)).status_code == 401
    assert (await api.get(EXPORT)).status_code == 401
    assert (await api.get(EXPORT, headers=bearer("not-a-token"))).status_code == 401
    missing = await api.get(EXPORT, headers=headers)
    assert missing.status_code == 404
    assert missing.json()["code"] == "identity.export_not_found"
    assert (await api.post("/v1/auth/logout", headers=headers)).status_code == 204
    assert (await api.post(EXPORT, headers=headers)).status_code == 401


async def test_more_than_five_requests_a_day_get_429(
    clients: ClientFactory, google: FakeGoogle, store: ObjectStore, valkey: Redis
) -> None:
    api, headers, _ = await signed_in(clients, google, store, valkey=valkey)
    for _ in range(5):
        assert (await api.post(EXPORT, headers=headers)).status_code == 202
    refused = await api.post(EXPORT, headers=headers)
    assert refused.status_code == 429
    assert refused.json()["code"] == "identity.export_rate_limited"
    assert 0 < int(refused.headers["retry-after"]) <= 86400
    assert (await api.get(EXPORT, headers=headers)).status_code == 200  # still answered


# --- build and download -----------------------------------------------------------------------


async def test_the_build_writes_one_file_with_each_section_and_get_links_it(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api, headers, user_id = await signed_in(clients, google, store)
    assert (await onboard(api, headers)).status_code == 201
    order_id = await activity(db_session, user_id)
    export_id = (await api.post(EXPORT, headers=headers)).json()["id"]
    queued = (await api.get(EXPORT, headers=headers)).json()
    assert queued["status"] == "queued" and queued["download"] is None

    run = await build(db_session, store)
    assert (run.status, run.summary) == ("ok", {"ready": 1, "retry": 0, "failed": 0})

    ready = (await api.get(EXPORT, headers=headers)).json()
    assert ready["id"] == export_id and ready["status"] == "ready"
    key = f"data_export/{user_id}/{export_id}.json"
    link = urlsplit(ready["download"]["url"])
    assert link.path == f"/gb-private/{key}"
    assert parse_qs(link.query)["X-Amz-Expires"] == ["300"]
    assert ready["download"]["size_bytes"] == len(s3.objects[key][1])
    ready_at = datetime.fromisoformat(ready["ready_at"])
    assert datetime.fromisoformat(ready["expires_at"]) == ready_at + timedelta(days=7)

    document = s3.file(key)
    assert document["format"] == "gharbasket-export/v1"
    assert document["user_id"] == str(user_id)
    sections = document["sections"]
    assert set(sections) == {"identity", "subscriptions", "orders", "billing", "notifications"}
    assert sections["notifications"] == {"messages": []}
    assert sections["identity"]["user"]["phone_e164"] == "+919800000001"
    assert sections["identity"]["household"]["address"]["flat"] == "A-1203"
    assert sections["identity"]["household"]["address"]["location"] == {
        "lat": 19.0396,
        "lng": 73.0676,
    }
    assert {c["purpose"] for c in sections["identity"]["consents"]} >= {"service_delivery"}
    assert [m["health_tags"] for m in sections["identity"]["household"]["members"]] == [
        ["diabetes"],
        [],
    ]
    (cycle,) = sections["subscriptions"]["cycles"]
    assert len(cycle["lines"]) == 1 and "plan_prediction_id" not in cycle  # no ML data
    assert sections["subscriptions"]["subscription"]["payment_mode"] == "wallet"
    (order,) = sections["orders"]["orders"]
    assert order["id"] == str(order_id) and len(order["lines"]) == 1
    assert [(e["to_status"], e["actor"]) for e in order["timeline"]] == [("delivered", "rider")]
    assert "actor_user_id" not in order["timeline"][0]  # the actor kind only, never who
    (issue,) = sections["orders"]["issues"]
    assert issue["order_id"] == str(order_id) and issue["kind"] == "missing"
    assert issue["photo_count"] == 1 and "photo_keys" not in issue  # counted, never included
    assert "resolver_user_id" not in issue
    assert [(item["qty"], item["unit_price_paise"]) for item in issue["items"]] == [(1, 3_000)]
    billing = sections["billing"]
    assert billing["wallet_balance_paise"] == 50_000
    assert [entry["amount_paise"] for entry in billing["wallet_entries"]] == [50_000]
    assert billing["topups"][0]["provider_payment_id"].startswith("pay_")

    upload = await db_session.get(Upload, key)
    assert upload is not None and upload.claimed_by == "identity"
    assert upload.purpose == "data_export" and upload.delete_after is not None
    minted = await db_session.execute(
        select(AuditEntry).where(AuditEntry.action == "identity.data_export_link_minted")
    )
    assert len(minted.scalars().all()) == 1


async def test_tokens_and_other_households_are_left_out(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api, headers, me = await signed_in(clients, google, store, n=1)
    _, other_headers, other = await signed_in(clients, google, store, n=2)
    for h in (headers, other_headers):
        assert (await onboard(api, h)).status_code == 201
    my_order = await activity(db_session, me)
    their_order = await activity(db_session, other)
    their_household = (
        await db_session.execute(select(Household.id).where(Household.owner_user_id == other))
    ).scalar_one()
    export_id = (await api.post(EXPORT, headers=headers)).json()["id"]
    await build(db_session, store)

    text = s3.objects[f"data_export/{me}/{export_id}.json"][1].decode()
    assert str(my_order) in text
    for theirs in (other, their_order, their_household, "+919800000002"):
        assert str(theirs) not in text
    assert "push-secret" not in text and "token" not in text
    devices = json.loads(text)["sections"]["identity"]["devices"]
    assert len(devices) == 1 and devices[0]["platform"] == "android"
    assert "idempotency_key" not in text and "h3_r9" not in text


async def test_health_tags_appear_only_with_consent(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api, headers, user_id = await signed_in(clients, google, store)
    assert (await onboard(api, headers)).status_code == 201
    # The consent ends without the usual erase: the export itself must still hide the tags.
    await db_session.execute(
        update(Consent)
        .where(Consent.user_id == user_id, Consent.purpose == "health_personalisation")
        .values(revoked_at=datetime.now(UTC))
    )
    export_id = (await api.post(EXPORT, headers=headers)).json()["id"]
    await build(db_session, store)
    members = s3.file(f"data_export/{user_id}/{export_id}.json")["sections"]["identity"][
        "household"
    ]["members"]
    assert [m["health_tags"] for m in members] == [[], []]


async def test_the_file_is_kept_seven_days(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api, headers, user_id = await signed_in(clients, google, store)
    export_id = (await api.post(EXPORT, headers=headers)).json()["id"]
    await build(db_session, store)
    key = f"data_export/{user_id}/{export_id}.json"
    export = await db_session.get(DataExport, uuid.UUID(export_id))
    assert export is not None and export.expires_at is not None
    expires_at = export.expires_at

    early = expires_at - timedelta(minutes=1)
    run = await run_job(db_session, "expire_data_exports", settings=SETTINGS, scheduled_for=early)
    assert run.summary == {"expired": 0}
    await run_job(
        db_session, "sweep_uploads", settings=SETTINGS, storage=store, scheduled_for=early
    )
    assert key in s3.objects

    late = expires_at + timedelta(minutes=1)
    run = await run_job(db_session, "expire_data_exports", settings=SETTINGS, scheduled_for=late)
    assert run.summary == {"expired": 1}
    await run_job(db_session, "sweep_uploads", settings=SETTINGS, storage=store, scheduled_for=late)
    assert key not in s3.objects
    gone = (await api.get(EXPORT, headers=headers)).json()
    assert gone["status"] == "expired" and gone["download"] is None


async def test_a_ready_export_past_its_expiry_reads_expired_before_the_job(
    clients: ClientFactory, google: FakeGoogle, store: ObjectStore, db_session: AsyncSession
) -> None:
    api, headers, _ = await signed_in(clients, google, store)
    export_id = uuid.UUID((await api.post(EXPORT, headers=headers)).json()["id"])
    await build(db_session, store)
    await db_session.execute(
        update(DataExport)
        .where(DataExport.id == export_id)
        .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    )
    answer = (await api.get(EXPORT, headers=headers)).json()
    assert answer["status"] == "expired" and answer["download"] is None


async def test_a_failing_build_retries_three_times_then_asking_again_queues_it(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api, headers, user_id = await signed_in(clients, google, store)
    export_id = (await api.post(EXPORT, headers=headers)).json()["id"]
    s3.fail_put = True
    summaries = [(await build(db_session, store)).summary for _ in range(3)]
    assert summaries == [
        {"ready": 0, "retry": 1, "failed": 0},
        {"ready": 0, "retry": 1, "failed": 0},
        {"ready": 0, "retry": 0, "failed": 1},
    ]
    assert (await build(db_session, store)).summary == {"ready": 0, "retry": 0, "failed": 0}
    failed = (await api.get(EXPORT, headers=headers)).json()
    assert failed["status"] == "failed" and failed["download"] is None
    assert await db_session.get(Upload, f"data_export/{user_id}/{export_id}.json") is None

    again = (await api.post(EXPORT, headers=headers)).json()
    assert again["id"] == export_id and again["status"] == "queued"
    s3.fail_put = False
    assert (await build(db_session, store)).summary == {"ready": 1, "retry": 0, "failed": 0}
    events = await db_session.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "identity.data_export_requested")
    )
    assert len(events.scalars().all()) == 2


async def test_the_build_needs_storage(db_session: AsyncSession) -> None:
    run = await run_job(db_session, "build_data_export", settings=SETTINGS)
    assert run.status == "failed" and "storage" in (run.error or "")


async def test_a_user_without_a_household_gets_empty_sections(
    clients: ClientFactory,
    google: FakeGoogle,
    store: ObjectStore,
    s3: FakeS3,
    db_session: AsyncSession,
) -> None:
    api, headers, user_id = await signed_in(clients, google, store)
    export_id = (await api.post(EXPORT, headers=headers)).json()["id"]
    await build(db_session, store)
    sections = s3.file(f"data_export/{user_id}/{export_id}.json")["sections"]
    assert sections["identity"]["household"] is None
    assert sections["subscriptions"]["cycles"] == [] and sections["orders"]["orders"] == []
    assert sections["billing"] == {"wallet_balance_paise": 0, "wallet_entries": [], "topups": []}
    assert (await db_session.get(User, user_id)) is not None
