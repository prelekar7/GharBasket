"""PATCH /v1/me, /v1/me/roles, /v1/me/devices and admin role grants (M1 US-5, US-6, US-8)."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.platform.db import create_engine
from gharbasket.platform.models import AuditEntry
from gharbasket.platform.tokens import verify_access_token
from gharbasket.tools.grant_role import main as grant_role_command
from gharbasket.tools.grant_role import run as grant_role_run

from ..conftest import make_settings
from .conftest import ClientFactory, bearer, sign_in
from .fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

ADMIN_PHONE = "+919800000010"
STAFF_PHONE = "+919800000011"
HUB = uuid.UUID("0199a1b2-0000-7000-8000-0000000000aa")


# --- profile ----------------------------------------------------------------------------------


async def test_patch_me_changes_only_the_fields_sent(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    auth = bearer((await sign_in(api, google))["access_token"])

    named = await api.patch("/v1/me", json={"name": "  Priya   Sharma "}, headers=auth)
    localised = await api.patch(
        "/v1/me", json={"locale": "mr-IN", "senior_mode": True}, headers=auth
    )
    me = await api.get("/v1/me", headers=auth)

    assert named.status_code == localised.status_code == 200
    assert named.json()["name"] == "Priya Sharma"
    assert me.json()["name"] == "Priya Sharma"  # untouched by the second request
    assert (me.json()["locale"], me.json()["senior_mode"]) == ("mr-IN", True)


@pytest.mark.parametrize(
    "body",
    [
        {"name": "   "},
        {"locale": "fr-FR"},
        {"locale": None},
        {"senior_mode": None},
        {"phone_e164": "x"},
    ],
    ids=["blank-name", "unknown-locale", "null-locale", "null-senior", "read-only-field"],
)
async def test_invalid_profile_changes_are_422(
    clients: ClientFactory, google: FakeGoogle, body: dict[str, Any]
) -> None:
    api = clients()
    auth = bearer((await sign_in(api, google))["access_token"])

    response = await api.patch("/v1/me", json=body, headers=auth)

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"


async def test_name_can_be_cleared(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    auth = bearer((await sign_in(api, google))["access_token"])
    await api.patch("/v1/me", json={"name": "Priya"}, headers=auth)

    cleared = await api.patch("/v1/me", json={"name": None}, headers=auth)

    assert cleared.json()["name"] is None


# --- devices ----------------------------------------------------------------------------------


async def test_devices_list_and_sign_out_another_device(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    phone = await sign_in(api, google)
    browser = await sign_in(api, google)
    auth = bearer(phone["access_token"])

    devices = (await api.get("/v1/me/devices", headers=auth)).json()
    browser_id = next(d["id"] for d in devices if not d["current"])
    removed = await api.delete(f"/v1/me/devices/{browser_id}", headers=auth)
    after = (await api.get("/v1/me/devices", headers=auth)).json()
    browser_refresh = await api.post(
        "/v1/auth/refresh", json={"refresh_token": browser["refresh_token"]}
    )
    again = await api.delete(f"/v1/me/devices/{browser_id}", headers=auth)

    assert len(devices) == 2 and sum(d["current"] for d in devices) == 1
    assert set(devices[0]) == {"id", "platform", "signed_in_at", "last_seen_at", "current"}
    assert removed.status_code == 204
    assert [d["current"] for d in after] == [True]
    assert browser_refresh.status_code == 401
    assert again.status_code == 404
    assert again.json()["code"] == "identity.device_not_found"


async def test_you_cannot_sign_out_someone_elses_device(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    mine = await sign_in(api, google)
    theirs = await sign_in(api, google, phone_number="+919800000099", sub="other-uid")
    their_device = (await api.get("/v1/me/devices", headers=bearer(theirs["access_token"]))).json()

    response = await api.delete(
        f"/v1/me/devices/{their_device[0]['id']}", headers=bearer(mine["access_token"])
    )
    malformed = await api.delete("/v1/me/devices/not-a-uuid", headers=bearer(mine["access_token"]))

    assert response.status_code == 404  # not 403: never reveal that the id exists
    assert malformed.status_code == 422


# --- roles and admin grants -------------------------------------------------------------------


async def _admin(api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession) -> dict[str, str]:
    """An admin's headers: sign in, get the admin role, refresh to carry it in the token."""
    signed_in = await sign_in(api, google, phone_number=ADMIN_PHONE, sub="admin-uid")
    user = (await db.execute(select(User).where(User.phone_e164 == ADMIN_PHONE))).scalar_one()
    db.add(UserRole(user_id=user.id, role="admin"))
    await db.flush()
    refreshed = await api.post(
        "/v1/auth/refresh", json={"refresh_token": signed_in["refresh_token"]}
    )
    return bearer(refreshed.json()["access_token"])


def _grant(**body: Any) -> dict[str, Any]:
    return {"phone_e164": STAFF_PHONE, "role": "hub_manager", "scope_id": str(HUB), **body}


async def test_an_admin_grants_a_hub_manager_role_and_it_is_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    staff = await sign_in(api, google, phone_number=STAFF_PHONE, sub="staff-uid")
    admin = await _admin(api, google, db_session)
    headers = {**admin, "Idempotency-Key": "grant-1"}

    granted = await api.post("/v1/admin/user-roles", json=_grant(), headers=headers)
    replayed = await api.post("/v1/admin/user-roles", json=_grant(), headers=headers)
    roles = (await api.get("/v1/me/roles", headers=bearer(staff["access_token"]))).json()
    refreshed = await api.post("/v1/auth/refresh", json={"refresh_token": staff["refresh_token"]})

    assert granted.status_code == 201
    assert granted.json()["role"] == "hub_manager"
    assert granted.json()["scope_type"] == "hub"
    assert replayed.headers["idempotent-replayed"] == "true"
    assert {
        "role": "hub_manager",
        "scope_type": "hub",
        "scope_id": str(HUB),
        "status": "verified",
    } in roles
    claims = verify_access_token(make_settings(), refreshed.json()["access_token"])
    assert f"hub_manager:{HUB}" in claims.roles
    audit = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.entity_id == granted.json()["user_id"])
        )
    ).scalar_one()
    assert audit.action == "roles.granted"


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"scope_id": None}, 422, "identity.invalid_scope"),
        ({"role": "admin"}, 422, "identity.invalid_scope"),
        # A staff role for an unknown number waits instead (M1 §3.6); a partner role needs the account.
        ({"phone_e164": "+919811111111", "role": "rider"}, 404, "identity.user_not_found"),
        ({"role": "customer"}, 422, "request.invalid"),
    ],
    ids=[
        "hub-role-without-hub",
        "admin-with-scope",
        "unknown-user-partner",
        "customer-not-grantable",
    ],
)
async def test_bad_grants_are_refused(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    api = clients()
    await sign_in(api, google, phone_number=STAFF_PHONE, sub="staff-uid")
    admin = await _admin(api, google, db_session)

    response = await api.post(
        "/v1/admin/user-roles", json=_grant(**body), headers={**admin, "Idempotency-Key": "g"}
    )

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_granting_the_same_role_twice_is_a_conflict(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    await sign_in(api, google, phone_number=STAFF_PHONE, sub="staff-uid")
    admin = await _admin(api, google, db_session)

    await api.post("/v1/admin/user-roles", json=_grant(), headers={**admin, "Idempotency-Key": "a"})
    second = await api.post(
        "/v1/admin/user-roles", json=_grant(), headers={**admin, "Idempotency-Key": "b"}
    )

    assert second.status_code == 409
    assert second.json()["code"] == "identity.role_exists"


async def test_only_admins_can_grant_roles(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    customer = await sign_in(api, google)

    response = await api.post(
        "/v1/admin/user-roles",
        json=_grant(),
        headers={**bearer(customer["access_token"]), "Idempotency-Key": "x"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "auth.forbidden"


# --- the operator command for the first admin -------------------------------------------------


async def test_the_grant_role_command_creates_the_first_admin(database_url: str) -> None:
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            user, _ = await find_or_create_user(setup, phone)
            await setup.commit()

        message = await grant_role_run(phone, "admin", None, database_url)

        async with AsyncSession(engine) as check:
            roles = await check.execute(select(UserRole.role).where(UserRole.user_id == user.id))
            assert sorted(roles.scalars()) == ["admin", "customer"]
            await check.execute(delete(AuditEntry).where(AuditEntry.entity_id == str(user.id)))
            await check.execute(delete(User).where(User.id == user.id))
            await check.commit()
        assert message == f"granted admin to user {user.id}"
    finally:
        await engine.dispose()


def test_the_command_refuses_a_shared_database_without_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    neon = "postgresql://app:pw@ep-x.ap-southeast-1.aws.neon.tech/db"
    # Both, so a developer's local .env cannot point the command back at Docker.
    monkeypatch.setenv("DATABASE_URL", neon)
    monkeypatch.setenv("DATABASE_URL_DIRECT", neon)

    with pytest.raises(SystemExit) as exited:
        grant_role_command([ADMIN_PHONE, "admin"])

    assert exited.value.code == 2
