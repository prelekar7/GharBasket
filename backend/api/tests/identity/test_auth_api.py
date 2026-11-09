"""Sign-in endpoints and /v1/me, matching the frozen walking-skeleton contract (M1 spec §3.1)."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import ColumnElement, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.modules.identity.models import LoginSession, PendingRoleGrant, User, UserRole

from ..conftest import make_settings
from .conftest import ANDROID, ClientFactory, bearer, sign_in
from .fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def test_first_exchange_signs_up_and_matches_the_frozen_contract(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()

    first = await sign_in(api, google)
    again = await sign_in(api, google)

    assert set(first) == {
        "access_token",
        "access_expires_in",
        "refresh_token",
        "is_new_user",
        "user",
        "deletion_cancelled",  # added by §3.5 (account deletion)
    }
    assert set(first["user"]) == {"id", "phone_e164", "name", "locale", "senior_mode"}
    assert first["access_expires_in"] == 900
    assert first["is_new_user"] is True and again["is_new_user"] is False
    assert first["user"] == {
        "id": first["user"]["id"],
        "phone_e164": "+919800000001",
        "name": None,
        "locale": "en-IN",
        "senior_mode": False,
    }
    assert again["user"]["id"] == first["user"]["id"]


async def test_me_returns_the_user_and_their_roles(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    signed_in = await sign_in(api, google)

    me = await api.get("/v1/me", headers=bearer(signed_in["access_token"]))

    assert me.status_code == 200
    assert me.json()["phone_e164"] == "+919800000001"
    assert me.json()["roles"] == [
        {"role": "customer", "scope_type": None, "scope_id": None, "status": "verified"}
    ]


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer not-a-token"}, {"Authorization": "Basic abc"}],
    ids=["no-token", "garbage", "wrong-scheme"],
)
async def test_me_needs_a_valid_access_token(
    clients: ClientFactory, headers: dict[str, str]
) -> None:
    response = await clients().get("/v1/me", headers=headers)

    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"
    assert response.headers["www-authenticate"] == "Bearer"


async def test_refresh_rotates_and_a_replayed_token_ends_the_session(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    signed_in = await sign_in(api, google)
    old = signed_in["refresh_token"]

    first = await api.post("/v1/auth/refresh", json={"refresh_token": old})
    second = await api.post(
        "/v1/auth/refresh", json={"refresh_token": first.json()["refresh_token"]}
    )
    replay = await api.post("/v1/auth/refresh", json={"refresh_token": old})

    assert first.status_code == second.status_code == 200
    assert set(first.json()) == {"access_token", "access_expires_in", "refresh_token"}
    assert replay.status_code == 401
    assert replay.json()["code"] == "auth.refresh_reused"
    me = await api.get("/v1/me", headers=bearer(second.json()["access_token"]))
    assert me.status_code == 401  # the whole session ended


async def test_logout_ends_this_device_and_logout_all_ends_every_device(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    phone = await sign_in(api, google)
    browser = await sign_in(api, google)
    tablet = await sign_in(api, google)

    logout = await api.post("/v1/auth/logout", headers=bearer(browser["access_token"]))
    after_logout = await api.get("/v1/me", headers=bearer(browser["access_token"]))
    still_in = await api.get("/v1/me", headers=bearer(phone["access_token"]))
    logout_all = await api.post("/v1/auth/logout-all", headers=bearer(phone["access_token"]))
    tablet_refresh = await api.post(
        "/v1/auth/refresh", json={"refresh_token": tablet["refresh_token"]}
    )

    assert logout.status_code == logout_all.status_code == 204
    assert after_logout.status_code == 401
    assert still_in.status_code == 200
    assert tablet_refresh.status_code == 401


async def test_dev_login_works_only_with_app_env_dev(clients: ClientFactory) -> None:
    body = {"dev_phone": "98000 00002", "dev_name": "Priya", "device": ANDROID}

    dev = await clients(app_env="dev").post("/v1/auth/exchange", json=body)
    test = await clients(app_env="test").post("/v1/auth/exchange", json=body)

    assert dev.status_code == 200
    assert dev.json()["user"]["phone_e164"] == "+919800000002"
    assert dev.json()["user"]["name"] == "Priya"
    assert test.status_code == 403
    assert test.json()["code"] == "auth.dev_login_disabled"


@pytest.mark.parametrize(
    "body",
    [
        {"device": ANDROID},
        {"firebase_id_token": "t", "dev_phone": "+919800000002", "device": ANDROID},
        {"dev_phone": "12345", "device": ANDROID},
        {"firebase_id_token": "t", "device": {"platform": "ios"}},
    ],
    ids=["neither", "both", "bad-phone", "unknown-platform"],
)
async def test_malformed_exchange_requests_are_422(
    clients: ClientFactory, body: dict[str, object]
) -> None:
    response = await clients(app_env="dev").post("/v1/auth/exchange", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"
    assert "12345" not in response.text  # submitted values are never echoed


async def test_a_bad_firebase_token_is_401(clients: ClientFactory, google: FakeGoogle) -> None:
    response = await clients().post(
        "/v1/auth/exchange",
        json={"firebase_id_token": google.id_token(aud="other-project"), "device": ANDROID},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"


async def test_without_firebase_configured_phone_sign_in_is_503(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    response = await clients(firebase=False).post(
        "/v1/auth/exchange", json={"firebase_id_token": google.id_token(), "device": ANDROID}
    )

    assert response.status_code == 503
    assert response.json()["code"] == "auth.provider_unavailable"


async def test_a_blocked_account_cannot_sign_in(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    await sign_in(api, google)
    await db_session.execute(update(User).values(status="blocked"))

    response = await api.post(
        "/v1/auth/exchange", json={"firebase_id_token": google.id_token(), "device": ANDROID}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "auth.account_disabled"


STAFF_PHONE = "+919800000007"


async def console_exchange(
    api: httpx2.AsyncClient, google: FakeGoogle, phone: str
) -> httpx2.Response:
    """The console's exchange: a Firebase ID token for `phone`, with staff_only."""
    return await api.post(
        "/v1/auth/exchange",
        json={
            "firebase_id_token": google.id_token(phone_number=phone, sub=f"uid-{phone}"),
            "device": {"platform": "web", "push_token": None},
            "staff_only": True,
        },
    )


async def count(db: AsyncSession, model: type, *where: ColumnElement[bool]) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_staff_only_refuses_a_new_number_without_creating_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    response = await console_exchange(clients(), google, STAFF_PHONE)

    assert response.status_code == 403
    assert response.json()["code"] == "auth.not_staff"
    assert "set-cookie" not in response.headers
    assert await count(db_session, User, User.phone_e164 == STAFF_PHONE) == 0


async def test_staff_only_lets_a_pending_grant_sign_up_and_claims_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    db_session.add(
        PendingRoleGrant(
            phone_e164=STAFF_PHONE,
            role="catalog_editor",
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
    )
    await db_session.flush()

    response = await console_exchange(clients(), google, STAFF_PHONE)

    assert response.status_code == 200, response.text
    assert response.json()["is_new_user"] is True
    user_id = (
        await db_session.execute(select(User.id).where(User.phone_e164 == STAFF_PHONE))
    ).scalar_one()
    roles = (
        await db_session.execute(select(UserRole.role).where(UserRole.user_id == user_id))
    ).scalars()
    assert sorted(roles) == ["catalog_editor", "customer"]


async def test_staff_only_refuses_an_expired_pending_grant(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    db_session.add(
        PendingRoleGrant(
            phone_e164=STAFF_PHONE,
            role="hub_manager",
            scope_type="hub",
            scope_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
    )
    await db_session.flush()

    response = await console_exchange(clients(), google, STAFF_PHONE)

    assert response.status_code == 403
    assert await count(db_session, User, User.phone_e164 == STAFF_PHONE) == 0


async def test_staff_only_refuses_a_customer_without_starting_a_session(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    await sign_in(api, google, phone_number=STAFF_PHONE, sub=f"uid-{STAFF_PHONE}")
    before = await count(db_session, LoginSession)

    response = await console_exchange(api, google, STAFF_PHONE)

    assert response.status_code == 403
    assert response.json()["code"] == "auth.not_staff"
    assert await count(db_session, LoginSession) == before


@pytest.mark.parametrize(
    ("status", "allowed"),
    [("verified", True), ("suspended", False), ("applied", False)],
)
async def test_staff_only_needs_a_verified_staff_role(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    status: str,
    allowed: bool,
) -> None:
    api = clients()
    signed_up = await sign_in(api, google, phone_number=STAFF_PHONE, sub=f"uid-{STAFF_PHONE}")
    db_session.add(
        UserRole(user_id=uuid.UUID(signed_up["user"]["id"]), role="admin", status=status)
    )
    await db_session.flush()

    response = await console_exchange(api, google, STAFF_PHONE)

    assert response.status_code == (200 if allowed else 403), response.text


async def test_without_staff_only_a_new_number_still_signs_up(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    signed_in = await sign_in(clients(), google, phone_number=STAFF_PHONE, sub=f"uid-{STAFF_PHONE}")

    assert signed_in["is_new_user"] is True


def test_the_openapi_contract_documents_the_frozen_shapes() -> None:
    schemas = create_app(make_settings()).openapi()["components"]["schemas"]

    assert set(schemas["ExchangeIn"]["properties"]) == {
        "firebase_id_token",
        "dev_phone",
        "dev_name",
        "device",
        "keep_account",  # added by §3.5
        "staff_only",  # added for the console's sign-in (§3.1)
    }
    assert set(schemas["ExchangeOut"]["required"]) == {
        "access_token",
        "access_expires_in",
        "refresh_token",
        "is_new_user",
        "user",
    }
    assert set(schemas["UserOut"]["required"]) == {
        "id",
        "phone_e164",
        "name",
        "locale",
        "senior_mode",
    }
    assert schemas["UserOut"]["properties"]["locale"]["enum"] == ["en-IN", "hi-IN", "mr-IN"]
