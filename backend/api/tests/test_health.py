import re

import pytest
from fastapi.testclient import TestClient

from gharbasket import __version__
from gharbasket.main import create_app

from .conftest import DEPLOYED_SECRETS, make_settings


def test_health_reports_ok(client: TestClient) -> None:
    response = client.get("/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "gharbasket-api",
        "version": __version__,
        "environment": "test",
        "commit": None,
    }


@pytest.mark.parametrize("variable", ["RENDER_GIT_COMMIT", "GIT_COMMIT"])
def test_health_reports_the_deployed_commit(variable: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(variable, "0cc5177abc")
    with TestClient(create_app(make_settings())) as client:
        assert client.get("/v1/health").json()["commit"] == "0cc5177abc"


def test_an_empty_commit_is_reported_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_COMMIT", "")
    with TestClient(create_app(make_settings())) as client:
        assert client.get("/v1/health").json()["commit"] is None


def test_every_response_gets_a_request_id(client: TestClient) -> None:
    response = client.get("/v1/health")

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


def test_safe_incoming_request_id_is_kept(client: TestClient) -> None:
    response = client.get("/v1/health", headers={"X-Request-ID": "app-7f3a.1"})

    assert response.headers["x-request-id"] == "app-7f3a.1"


def test_unsafe_incoming_request_id_is_replaced(client: TestClient) -> None:
    response = client.get("/v1/health", headers={"X-Request-ID": "x" * 200})

    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-request-id"])


def test_trailing_slash_is_not_redirected(client: TestClient) -> None:
    # A redirect would send browsers to the API host directly and lose the proxy's cookies.
    response = client.get("/v1/health/", follow_redirects=False)

    assert response.status_code == 404


def test_api_docs_are_hidden_in_production() -> None:
    with TestClient(create_app(make_settings(app_env="prod", **DEPLOYED_SECRETS))) as prod_client:
        assert prod_client.get("/v1/openapi.json").status_code == 404
        assert prod_client.get("/v1/docs").status_code == 404
        assert prod_client.get("/v1/health").status_code == 200


def test_cors_allows_only_the_capacitor_origin(client: TestClient) -> None:
    preflight = {"Access-Control-Request-Method": "GET"}

    allowed = client.options("/v1/health", headers={"Origin": "https://localhost", **preflight})
    blocked = client.options("/v1/health", headers={"Origin": "https://evil.example", **preflight})

    assert allowed.headers["access-control-allow-origin"] == "https://localhost"
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in blocked.headers
