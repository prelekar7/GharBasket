"""Push tokens on sessions, the `whatsapp` consent, and the facades M11 sends with (M11 §3.1)."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import LoginSession, User

from .conftest import ClientFactory, bearer
from .fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

PHONE = "+919800012101"


async def device(
    api: httpx2.AsyncClient, google: FakeGoogle, token: str | None, phone: str = PHONE
) -> dict[str, Any]:
    """Sign a phone in with this FCM token; returns the exchange answer."""
    response = await api.post(
        "/v1/auth/exchange",
        json={
            "firebase_id_token": google.id_token(phone_number=phone, sub=f"uid{phone}"),
            "device": {"platform": "android", "push_token": token},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


async def session_of(api: httpx2.AsyncClient, signed: dict[str, Any]) -> uuid.UUID:
    devices = (await api.get("/v1/me/devices", headers=bearer(signed["access_token"]))).json()
    return uuid.UUID(next(d["id"] for d in devices if d["current"]))


async def tokens_by_session(db: AsyncSession, user_id: uuid.UUID) -> dict[uuid.UUID, str | None]:
    rows = await db.execute(
        select(LoginSession.id, LoginSession.push_token).where(LoginSession.user_id == user_id)
    )
    return {session_id: token for session_id, token in rows.all()}


async def test_a_device_sets_and_clears_its_token_and_a_reinstall_takes_it_over(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    first = await device(api, google, None)
    user_id = uuid.UUID(first["user"]["id"])
    auth = bearer(first["access_token"])
    second = await device(api, google, "fcm-phone-b")

    assert (
        await api.put("/v1/me/push-token", json={"push_token": "fcm-phone-a"}, headers=auth)
    ).status_code == 204
    assert sorted(t for t in (await tokens_by_session(db_session, user_id)).values() if t) == [
        "fcm-phone-a",
        "fcm-phone-b",
    ]
    targets = await identity_api.active_push_tokens(db_session, user_id)
    assert {t.token for t in targets} == {"fcm-phone-a", "fcm-phone-b"}
    assert {t.platform for t in targets} == {"android"}

    # The same phone signs in again after a reinstall: the old session no longer owns the token.
    third = await device(api, google, "fcm-phone-b")
    owners = {t: s for s, t in (await tokens_by_session(db_session, user_id)).items() if t}
    assert owners["fcm-phone-b"] == await session_of(api, third)
    assert await session_of(api, second) not in owners.values()

    # Turning notifications off on the phone clears the token; a bad body is refused.
    assert (
        await api.put("/v1/me/push-token", json={"push_token": None}, headers=auth)
    ).status_code == 204
    assert {t.token for t in await identity_api.active_push_tokens(db_session, user_id)} == {
        "fcm-phone-b"
    }
    empty = await api.put("/v1/me/push-token", json={"push_token": ""}, headers=auth)
    assert empty.status_code == 422
    assert (await clients().put("/v1/me/push-token", json={"push_token": "x"})).status_code == 401


async def test_signed_out_and_expired_devices_get_no_pushes_and_fcm_can_drop_a_token(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    kept = await device(api, google, "fcm-kept", phone="+919800012102")
    user_id = uuid.UUID(kept["user"]["id"])
    gone = await device(api, google, "fcm-signed-out", phone="+919800012102")
    old = await device(api, google, "fcm-expired", phone="+919800012102")
    old_session = await session_of(api, old)
    assert (
        await api.post("/v1/auth/logout", headers=bearer(gone["access_token"]))
    ).status_code == 204
    await db_session.execute(
        update(LoginSession)
        .where(LoginSession.id == old_session)
        .values(expires_at=LoginSession.created_at)
    )

    assert [t.token for t in await identity_api.active_push_tokens(db_session, user_id)] == [
        "fcm-kept"
    ]
    await identity_api.clear_push_token(db_session, "fcm-kept")
    assert await identity_api.active_push_tokens(db_session, user_id) == []


async def test_the_whatsapp_consent_and_the_locale_facade(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    signed = await device(api, google, None, phone="+919800012103")
    user_id, auth = uuid.UUID(signed["user"]["id"]), bearer(signed["access_token"])
    assert await identity_api.has_consent(db_session, user_id, "whatsapp") is False
    assert await identity_api.user_locale(db_session, user_id) == "en-IN"

    granted = await api.post(
        "/v1/consents",
        json={"purpose": "whatsapp", "version": "v1", "granted": True},
        headers=auth,
    )
    assert granted.status_code == 200, granted.text
    assert await identity_api.has_consent(db_session, user_id, "whatsapp") is True
    await api.post(
        "/v1/consents",
        json={"purpose": "whatsapp", "version": "v1", "granted": False},
        headers=auth,
    )
    assert await identity_api.has_consent(db_session, user_id, "whatsapp") is False

    await db_session.execute(update(User).where(User.id == user_id).values(locale="mr-IN"))
    assert await identity_api.user_locale(db_session, user_id) == "mr-IN"
    assert await identity_api.user_locale(db_session, uuid.uuid4()) == "en-IN"
