"""Text Postgres cannot store (NUL characters) is a 422, not a 500 (found by the fuzzer)."""

from collections.abc import AsyncIterator

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import get_session

from .conftest import make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def test_nul_characters_are_refused_as_invalid_input(db_session: AsyncSession) -> None:
    app = create_app(make_settings(app_env="dev"))

    async def session_override() -> AsyncIterator[AsyncSession]:
        try:
            yield db_session
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_session] = session_override
    transport = httpx2.ASGITransport(app=app)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as api:
        signed_in = await api.post(
            "/v1/auth/exchange",
            json={"dev_phone": "+919800000077", "device": {"platform": "android"}},
        )
        headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}

        name = await api.patch("/v1/me", json={"name": "Pri\u0000ya"}, headers=headers)
        search = await api.get("/v1/societies", params={"q": "sun\u0000rise"}, headers=headers)
        still_fine = await api.get("/v1/me", headers=headers)

    for response in (name, search):
        assert response.status_code == 422, response.text
        assert response.json()["code"] == "request.invalid"
    assert still_fine.status_code == 200
