"""The routes analysed once per process (tests/fast_app.py) still make every app a complete one.

The API contract test (tests/test_openapi.py) compares an app built from the kept routes with the
committed openapi.json; this adds that the kept routes serve requests the same in every app.
"""

import httpx2
import pytest

from gharbasket.main import create_app

from . import fast_app
from .conftest import add_test_routes, make_settings


@pytest.mark.anyio
async def test_apps_built_from_the_kept_routes_answer_alike() -> None:
    fast_app.install()
    answers: list[int] = []
    for env in ("test", "dev", "test"):
        app = create_app(make_settings(app_env=env))
        add_test_routes(app)
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            bad = await client.post("/v1/_test/profile", json={"name": 3})
            good = await client.post("/v1/_test/profile", json={"name": "Asha", "age": 41})
            answers += [bad.status_code, good.status_code]

    assert fast_app._found  # pyright: ignore[reportPrivateUsage]
    assert answers == [422, 200] * 3
