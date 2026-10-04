"""The ML service's HTTP surface: health, the service token, validation, and settings."""

import pytest
from fastapi.testclient import TestClient
from gharbasket_ml.main import create_app
from gharbasket_ml.settings import Settings
from pydantic import SecretStr, ValidationError

from .seed_weeks import request

TOKEN = "test-ml-token"


@pytest.fixture
def client() -> TestClient:
    settings = Settings(app_env="test", ml_service_token=SecretStr(TOKEN))
    return TestClient(create_app(settings))


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {
        "status": "ok",
        "service": "gharbasket-ml",
        "version": "0.1.0",
    }


def test_plan_week_needs_the_service_token(client: TestClient) -> None:
    body = request().model_dump()

    missing = client.post("/plan/week", json=body)
    wrong = client.post("/plan/week", json=body, headers={"Authorization": "Bearer nope"})
    right = client.post("/plan/week", json=body, headers={"Authorization": f"Bearer {TOKEN}"})

    for refused in (missing, wrong):
        assert refused.status_code == 401
        assert refused.json()["code"] == "ml.unauthorized"
        assert refused.headers["content-type"] == "application/problem+json"
    assert right.status_code == 200
    assert right.json()["status"] == "optimal"
    assert len(right.json()["plan"]) == 21


def test_invalid_requests_are_422(client: TestClient) -> None:
    body = request().model_dump() | {"time_limit_s": 30}

    response = client.post("/plan/week", json=body, headers={"Authorization": f"Bearer {TOKEN}"})

    assert response.status_code == 422


def test_without_a_token_only_local_development_works() -> None:
    with TestClient(create_app(Settings(app_env="dev"))) as local:
        assert local.post("/plan/week", json=request().model_dump()).status_code == 200
    for env in ("staging", "prod"):
        with pytest.raises(ValidationError, match="ML_SERVICE_TOKEN"):
            Settings(app_env=env)  # pyright: ignore[reportArgumentType]


def test_models_and_ai_health_list_the_built_in_solvers(client: TestClient, tmp_path) -> None:
    auth = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get("/models").status_code == 401

    models = client.get("/models", headers=auth).json()
    health = client.get("/health/ai", headers=auth).json()

    assert [(m["name"], m["version"], m["source"]) for m in models] == [
        ("planner", "cpsat-v0", "builtin"),
        ("routing", "cvrptw-waves-v0", "builtin"),
    ]
    assert health["status"] == "ok" and health["osrm"] == "not_configured"
    assert [m["name"] for m in health["models"]] == ["planner", "routing"]

    manifest = tmp_path / "latest.json"
    manifest.write_text('{"models": [{"name": "pantry", "version": "2026.10.04-1"}]}')
    settings = Settings(app_env="test", ml_service_token=SecretStr(TOKEN), models_manifest=manifest)
    with_manifest = TestClient(create_app(settings)).get("/models", headers=auth).json()
    assert with_manifest[-1] == {
        "name": "pantry",
        "version": "2026.10.04-1",
        "kind": "learned",
        "source": "manifest",
    }
