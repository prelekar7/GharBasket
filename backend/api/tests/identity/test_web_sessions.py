"""Browser sign-in: httpOnly cookies + CSRF, instant sign-out, sign-in rate limits (ADR-0024)."""

from typing import Any

import httpx2
import pytest
from fastapi import FastAPI
from redis.asyncio import Redis

from gharbasket.modules.identity import router as identity_router
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.rate_limit import private_key
from gharbasket.platform.valkey import create_valkey

from .conftest import ANDROID, ClientFactory, bearer, sign_in
from .fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

WEB = {"X-GB-Client": "web", "Origin": "http://localhost:5173"}
WEB_DEVICE = {"platform": "web", "push_token": None}


def _cookie_header(response: httpx2.Response, name: str) -> str:
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}="))


async def _web_sign_in(
    api: httpx2.AsyncClient, google: FakeGoogle, **claims: Any
) -> httpx2.Response:
    response = await api.post(
        "/v1/auth/exchange",
        json={"firebase_id_token": google.id_token(**claims), "device": WEB_DEVICE},
        headers=WEB,
    )
    assert response.status_code == 200, response.text
    return response


def _add_whoami(api: httpx2.AsyncClient) -> None:
    """A route that trusts only the access token (no database check), like most endpoints."""
    transport = api._transport  # pyright: ignore[reportPrivateUsage]
    assert isinstance(transport, httpx2.ASGITransport)
    app = transport.app
    assert isinstance(app, FastAPI)

    @app.get("/v1/_test/whoami")
    async def whoami(principal: CurrentPrincipal) -> str:
        return str(principal.user_id)


# --- cookies --------------------------------------------------------------------------------


async def test_browsers_get_httponly_cookies_and_no_tokens_in_the_body(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    response = await _web_sign_in(clients(), google)

    access = _cookie_header(response, "__Host-gb_at").lower()
    refresh = _cookie_header(response, "gb_rt").lower()
    assert response.json()["access_token"] is None
    assert response.json()["refresh_token"] is None
    assert response.json()["access_expires_in"] == 900
    for cookie in (access, refresh):
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        assert "domain=" not in cookie
    assert "path=/;" in access and "max-age=900" in access
    assert "path=/v1/auth" in refresh


async def test_a_browser_is_signed_in_by_its_cookie(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    await _web_sign_in(api, google)

    me = await api.get("/v1/me")  # no Authorization header: the cookie jar sends the cookie

    assert me.status_code == 200
    assert me.json()["phone_e164"] == "+919800000001"


async def test_cookie_requests_that_change_things_need_our_header_and_origin(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    await _web_sign_in(api, google)

    no_header = await api.patch("/v1/me", json={"name": "A"})
    other_site = await api.patch(
        "/v1/me",
        json={"name": "A"},
        headers={"X-GB-Client": "web", "Origin": "https://evil.example"},
    )
    ours = await api.patch("/v1/me", json={"name": "A"}, headers=WEB)
    same_origin_without_origin_header = await api.patch(
        "/v1/me", json={"name": "B"}, headers={"X-GB-Client": "web"}
    )

    assert no_header.status_code == other_site.status_code == 403
    assert no_header.json()["code"] == "auth.csrf_rejected"
    assert ours.status_code == same_origin_without_origin_header.status_code == 200


async def test_signing_in_from_another_site_is_refused(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    response = await clients().post(
        "/v1/auth/exchange",
        json={"firebase_id_token": google.id_token(), "device": WEB_DEVICE},
        headers={"X-GB-Client": "web", "Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "auth.csrf_rejected"
    assert "set-cookie" not in response.headers


async def test_deployed_origins_come_from_web_origins(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    console = "https://gharbasket-console.vercel.app"
    api = clients(web_origins=[console])

    ok = await api.post(
        "/v1/auth/exchange",
        json={"firebase_id_token": google.id_token(), "device": WEB_DEVICE},
        headers={"X-GB-Client": "web", "Origin": console},
    )

    assert ok.status_code == 200


async def test_browser_refresh_rotates_the_cookie_and_replay_ends_the_session(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    signed_in = await _web_sign_in(api, google)
    first_refresh_cookie = signed_in.cookies["gb_rt"]

    refreshed = await api.post("/v1/auth/refresh", json={}, headers=WEB)
    # Use the new refresh cookie too; replaying the first one is then theft, not a lost response.
    assert (await api.post("/v1/auth/refresh", json={}, headers=WEB)).status_code == 200
    replay = await api.post(
        "/v1/auth/refresh", json={"refresh_token": first_refresh_cookie}, headers=WEB
    )

    assert refreshed.status_code == 200
    assert refreshed.json()["refresh_token"] is None
    assert refreshed.cookies["gb_rt"] != first_refresh_cookie
    assert replay.status_code == 401
    assert replay.json()["code"] == "auth.refresh_reused"


@pytest.mark.parametrize(
    ("headers", "status", "code"),
    [({}, 403, "auth.csrf_rejected"), ({"X-GB-Client": "web"}, 401, "auth.invalid_token")],
    ids=["no-header", "no-cookie"],
)
async def test_browser_refresh_needs_the_header_and_the_cookie(
    clients: ClientFactory, headers: dict[str, str], status: int, code: str
) -> None:
    response = await clients().post("/v1/auth/refresh", json={}, headers=headers)

    assert response.status_code == status
    assert response.json()["code"] == code


async def test_browser_logout_clears_both_cookies(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    await _web_sign_in(api, google)

    logout = await api.post("/v1/auth/logout", headers=WEB)

    cleared = logout.headers.get_list("set-cookie")
    assert logout.status_code == 204
    assert any(c.startswith("__Host-gb_at=") and "Max-Age=0" in c for c in cleared)
    assert any(c.startswith("gb_rt=") and "Max-Age=0" in c for c in cleared)
    assert (await api.get("/v1/me")).status_code == 401


async def test_a_bearer_token_wins_over_a_cookie(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    await _web_sign_in(api, google)  # cookie for the first user
    other = await sign_in(api, google, phone_number="+919800000055", sub="uid-2")

    me = await api.get("/v1/me", headers=bearer(other["access_token"]))

    assert me.json()["phone_e164"] == "+919800000055"


# --- instant sign-out -------------------------------------------------------------------------


async def test_sign_out_takes_effect_at_once(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    _add_whoami(api)
    signed_in = await sign_in(api, google)
    auth = bearer(signed_in["access_token"])

    before = await api.get("/v1/_test/whoami", headers=auth)
    await api.post("/v1/auth/logout", headers=auth)
    after = await api.get("/v1/_test/whoami", headers=auth)

    assert before.status_code == 200
    assert after.status_code == 401
    assert after.json()["detail"] == "You have signed out on this device."


async def test_logout_everywhere_and_device_sign_out_also_take_effect_at_once(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    _add_whoami(api)
    phone = await sign_in(api, google)
    tablet = await sign_in(api, google)
    browser = await sign_in(api, google)
    devices = (await api.get("/v1/me/devices", headers=bearer(phone["access_token"]))).json()
    browser_device = devices[0]["id"] if not devices[0]["current"] else devices[1]["id"]

    await api.delete(f"/v1/me/devices/{browser_device}", headers=bearer(phone["access_token"]))
    await api.post("/v1/auth/logout-all", headers=bearer(phone["access_token"]))

    for signed_in in (phone, tablet, browser):
        whoami = await api.get("/v1/_test/whoami", headers=bearer(signed_in["access_token"]))
        assert whoami.status_code == 401


async def test_a_replayed_refresh_token_ends_the_session_at_once(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    _add_whoami(api)
    signed_in = await sign_in(api, google)
    fresh = (
        await api.post("/v1/auth/refresh", json={"refresh_token": signed_in["refresh_token"]})
    ).json()
    await api.post("/v1/auth/refresh", json={"refresh_token": fresh["refresh_token"]})

    await api.post("/v1/auth/refresh", json={"refresh_token": signed_in["refresh_token"]})
    whoami = await api.get("/v1/_test/whoami", headers=bearer(fresh["access_token"]))

    assert whoami.status_code == 401


# --- rate limits ------------------------------------------------------------------------------


async def test_sign_in_attempts_per_phone_are_limited(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity_router, "EXCHANGE_PER_PHONE", (3, 600))
    api = clients(valkey=valkey)
    body = {"firebase_id_token": google.id_token(), "device": ANDROID}

    statuses = [(await api.post("/v1/auth/exchange", json=body)).status_code for _ in range(4)]
    limited = await api.post("/v1/auth/exchange", json=body)
    keys = [
        key.decode() if isinstance(key, bytes) else str(key)
        for key in await valkey.keys("gb:rl:exchange:phone:*")
    ]

    assert statuses == [200, 200, 200, 429]
    assert limited.json()["code"] == "auth.rate_limited"
    assert 0 < int(limited.headers["retry-after"]) <= 600
    # The counter key never contains the phone number itself.
    assert keys and all("9800000001" not in key for key in keys)
    assert private_key("+919800000001") in keys[0]


async def test_refreshes_per_address_are_limited(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity_router, "REFRESH_PER_IP", (2, 60))
    api = clients(valkey=valkey)
    token = (await sign_in(api, google))["refresh_token"]

    codes = []
    for _ in range(3):
        response = await api.post("/v1/auth/refresh", json={"refresh_token": token})
        codes.append(response.status_code)
        if response.status_code == 200:
            token = response.json()["refresh_token"]

    assert codes == [200, 200, 429]


async def test_valkey_being_down_does_not_lock_anyone_out(
    clients: ClientFactory, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity_router, "EXCHANGE_PER_PHONE", (1, 600))
    down = create_valkey("redis://127.0.0.1:1/0")
    api = clients(valkey=down)
    _add_whoami(api)

    first = await sign_in(api, google)
    second = await sign_in(api, google)  # over the limit, but the limiter is unreachable
    whoami = await api.get("/v1/_test/whoami", headers=bearer(second["access_token"]))
    await down.aclose()

    assert first and second
    assert whoami.status_code == 200
