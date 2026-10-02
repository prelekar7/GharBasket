from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from gharbasket.main import create_app
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings


def make_settings(**overrides: object) -> Settings:
    """Settings for tests: never read a developer's .env file."""
    values: dict[str, object] = {"app_env": "test", "log_json": True, **overrides}
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def add_test_routes(app: FastAPI) -> None:
    @app.get("/v1/_test/problem")
    async def raise_problem() -> None:
        raise ProblemError(409, "orders.cutoff_passed", "Orders for this slot are already locked.")

    @app.get("/v1/_test/boom")
    async def raise_unexpected() -> None:
        raise RuntimeError("database password is hunter2")

    class Profile(BaseModel):
        name: str
        age: int

    @app.post("/v1/_test/profile")
    async def save_profile(profile: Profile) -> Profile:
        return profile


@pytest.fixture
def app() -> FastAPI:
    app = create_app(make_settings(cors_allow_origins=["https://localhost"]))
    add_test_routes(app)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
