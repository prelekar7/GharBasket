"""The ML service's HTTP surface: health, the service token, validation, and settings."""

import httpx2
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


# --- the Space: the road network's status and the smoke check -----------------------------------


@pytest.mark.anyio
async def test_health_ai_reports_the_road_network() -> None:
    from gharbasket_ml import registry, smoke

    def osrm(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"code": "Ok", "routes": []})

    def broken(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(500)

    assert await registry.osrm_status(None) == "not_configured"
    assert await registry.osrm_status("http://osrm", transport=httpx2.MockTransport(osrm)) == "ok"
    assert (
        await registry.osrm_status("http://osrm", transport=httpx2.MockTransport(broken)) == "down"
    )
    health = await registry.health(None, None)
    assert health["osrm"] == "not_configured" and health["status"] == "ok"

    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    lines = await smoke.check("http://ml", TOKEN, transport=httpx2.ASGITransport(app=app))
    assert len(lines) == 5 and all(" 200 " in line for line in lines), lines
    assert "osrm=not_configured" in lines[1] and "/plan/week 200" in lines[3]


def test_smoke_main_prints_a_line_per_endpoint_and_exits_0_when_all_answer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from gharbasket_ml import smoke

    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))

    code = smoke.main(["http://ml/", TOKEN], transport=httpx2.ASGITransport(app=app))

    out = capsys.readouterr().out.splitlines()
    assert code == 0
    assert [line.split()[0] for line in out] == [
        "/health",
        "/health/ai",
        "/models",
        "/plan/week",
        "/pantry/quantities",
    ]
    assert out[0] == "/health 200 0.1.0" and out[4].startswith("/pantry/quantities 200 mean=")


def test_smoke_main_reports_errors_per_call_instead_of_crashing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from gharbasket_ml import smoke

    def flaky(request: httpx2.Request) -> httpx2.Response:
        match request.url.path:
            case "/health":
                return httpx2.Response(200, text="<html>cold start</html>")
            case "/health/ai":
                raise httpx2.ReadTimeout("timed out", request=request)
            case "/models":
                return httpx2.Response(200, json=[{"name": "planner"}, "junk"])
            case "/plan/week":
                return httpx2.Response(503, text="building")
            case _:
                return httpx2.Response(200, json={"items": []})

    code = smoke.main(["http://ml"], transport=httpx2.MockTransport(flaky))

    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert out[0].startswith("/health ERR not JSON")
    assert out[1] == "/health/ai ERR ReadTimeout timed out"
    assert out[2] == "/models 200 ['planner']"
    assert out[3] == "/plan/week 503 None in None s"
    assert out[4] == "/pantry/quantities 200 mean=None"


def test_smoke_main_needs_a_url(capsys: pytest.CaptureFixture[str]) -> None:
    from gharbasket_ml import smoke

    assert smoke.main([]) == 2
    assert "usage" in capsys.readouterr().err
    assert not smoke.passed("/health ERR ConnectError") and smoke.passed("/health 200 0.1.0")
