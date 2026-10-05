"""Shared fixtures for identity tests: a fake Google and API clients on the rolled-back session."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import get_session
from gharbasket.platform.valkey import create_valkey

from ..conftest import make_settings
from .fake_firebase import FakeGoogle

ANDROID = {"platform": "android", "push_token": None}
ClientFactory = Callable[..., httpx2.AsyncClient]


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
async def valkey(valkey_url: str) -> AsyncIterator[Redis]:
    """The test Valkey database (index 15), emptied before each test."""
    client = create_valkey(valkey_url)
    await client.flushdb()
    yield client
    await client.aclose()


@pytest.fixture
async def clients(db_session: AsyncSession, google: FakeGoogle) -> AsyncIterator[ClientFactory]:
    """Build API clients for a given APP_ENV, all sharing the test's rolled-back session.

    HTTPS base URL, because browsers (and httpx2's cookie jar) only send Secure cookies over HTTPS.
    """
    opened: list[httpx2.AsyncClient] = []

    async def session_override() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise

    def make(
        app_env: str = "test",
        firebase: bool = True,
        valkey: Redis | None = None,
        headers: dict[str, str] | None = None,
        razorpay: Any = None,
        http: httpx2.AsyncClient | None = None,
        **settings: Any,
    ) -> httpx2.AsyncClient:
        app = create_app(make_settings(app_env=app_env, **settings))
        app.dependency_overrides[get_session] = session_override
        app.state.firebase_verifier = google.verifier() if firebase else None
        app.state.valkey = valkey
        app.state.razorpay = razorpay  # a RazorpayClient on a fake transport (billing tests)
        if http is not None:
            app.state.http = http  # outbound calls on a fake transport (the ML service)
        client = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="https://t", headers=headers
        )
        opened.append(client)
        return client

    yield make
    for client in opened:
        await client.aclose()


async def sign_in(api: httpx2.AsyncClient, google: FakeGoogle, **claims: Any) -> dict[str, Any]:
    response = await api.post(
        "/v1/auth/exchange",
        json={"firebase_id_token": google.id_token(**claims), "device": ANDROID},
    )
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}
