"""Shared fixtures for identity tests: a fake Google and API clients on the rolled-back session."""

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import get_session

from ..conftest import make_settings
from .fake_firebase import FakeGoogle

ANDROID = {"platform": "android", "push_token": None}
ClientFactory = Callable[..., httpx2.AsyncClient]


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


@pytest.fixture
async def clients(db_session: AsyncSession, google: FakeGoogle) -> AsyncIterator[ClientFactory]:
    """Build API clients for a given APP_ENV, all sharing the test's rolled-back session."""
    opened: list[httpx2.AsyncClient] = []

    async def session_override() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise

    def make(app_env: str = "test", firebase: bool = True) -> httpx2.AsyncClient:
        app = create_app(make_settings(app_env=app_env))
        app.dependency_overrides[get_session] = session_override
        app.state.firebase_verifier = google.verifier() if firebase else None
        client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://t")
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
