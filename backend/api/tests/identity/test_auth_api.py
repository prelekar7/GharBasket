"""Sign-in endpoints and /v1/me, matching the frozen walking-skeleton contract (M1 spec §3.1)."""

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.modules.identity.models import User

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


def test_the_openapi_contract_documents_the_frozen_shapes() -> None:
    schemas = create_app(make_settings()).openapi()["components"]["schemas"]

    assert set(schemas["ExchangeIn"]["properties"]) == {
        "firebase_id_token",
        "dev_phone",
        "dev_name",
        "device",
        "keep_account",  # added by §3.5
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
