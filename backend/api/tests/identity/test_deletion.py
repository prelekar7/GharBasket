"""Account deletion (M1 §3.5): the request, the grace period, cancelling and the purge."""

import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity import deletion
from gharbasket.modules.identity.models import (
    DataExport,
    Deletion,
    LoginSession,
    User,
    UserRole,
)
from gharbasket.platform.files import ObjectStore, create_store
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, OutboxEvent, Upload
from gharbasket.tools.seed import seed_societies

from ..conftest import make_settings
from .conftest import ANDROID, ClientFactory, bearer
from .fake_firebase import FakeGoogle
from .onboarding import onboard
from .test_data_export import FakeS3

pytestmark = [pytest.mark.anyio, pytest.mark.services]

SETTINGS = make_settings(
    s3_endpoint_url="http://seaweedfs:8333",
    s3_access_key="test-access",
    s3_secret_key="test-storage-secret",
)


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

    # Asking again reuses the row.
    assert (await api.delete("/v1/me", headers=bearer(again["access_token"]))).status_code == 202
    row = await db_session.get(Deletion, user_id, populate_existing=True)
    assert row is not None and row.cancelled_at is None
