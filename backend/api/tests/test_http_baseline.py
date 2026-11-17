"""What every response carries and what every request may send (M0 §6.4): the security headers,
the default `Cache-Control`, and the cap on request bodies, declared or streamed."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient

from gharbasket.main import create_app
from gharbasket.platform import body_limit
from gharbasket.platform.body_limit import MAX_BODY_BYTES, MAX_INTERNAL_BODY_BYTES

from .conftest import DEPLOYED_SECRETS, make_settings

BASELINE = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}
HSTS = "max-age=31536000; includeSubDomains"


def _add_routes(app: FastAPI) -> None:
    @app.get("/v1/_test/cached")
    async def cached(response: Response) -> dict[str, bool]:
        response.headers["Cache-Control"] = "public, max-age=300"
        return {"ok": True}

    @app.post("/v1/_test/raw")
    async def raw(request: Request) -> dict[str, int]:  # reads the body itself, like a webhook
        return {"size": len(await request.body())}


@pytest.fixture
def raw_client(app: FastAPI) -> Iterator[TestClient]:
    _add_routes(app)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _chunks(size: int, chunk: int = 64 * 1024) -> Iterator[bytes]:
    """A body without Content-Length: httpx sends it chunked."""
    sent = 0
    while sent < size:
        part = min(chunk, size - sent)
        sent += part
        yield b"x" * part


def test_every_response_carries_the_security_headers_and_no_store(raw_client: TestClient) -> None:
    answers = [
        raw_client.get("/v1/health"),
        raw_client.get("/v1/_test/problem"),  # a problem response
        raw_client.get("/v1/no-such-route"),  # Starlette's 404
        raw_client.get("/v1/_test/boom"),  # the generic 500
    ]

    for answer in answers:
        for name, value in BASELINE.items():
            assert answer.headers[name] == value, (answer.url, name)
        assert answer.headers["cache-control"] == "no-store", answer.url
        assert "strict-transport-security" not in answer.headers  # local runs are plain HTTP
    assert [a.status_code for a in answers] == [200, 409, 404, 500]


def test_a_route_that_chose_its_caching_keeps_it(raw_client: TestClient) -> None:
    answer = raw_client.get("/v1/_test/cached")

    assert answer.headers.get_list("cache-control") == ["public, max-age=300"]
    assert answer.headers["x-content-type-options"] == "nosniff"


def test_deployed_environments_send_hsts() -> None:
    for env in ("staging", "prod"):
        with TestClient(create_app(make_settings(app_env=env, **DEPLOYED_SECRETS))) as client:
            answer = client.get("/v1/health")
        assert answer.headers["strict-transport-security"] == HSTS, env
        assert answer.headers["x-content-type-options"] == "nosniff"


def test_a_declared_body_over_the_cap_is_refused_before_it_is_read(
    raw_client: TestClient,
) -> None:
    too_big = raw_client.post(
        "/v1/_test/raw",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={"Content-Type": "application/octet-stream"},
    )
    at_the_cap = raw_client.post("/v1/_test/raw", content=b"x" * MAX_BODY_BYTES)

    assert too_big.status_code == 413
    assert too_big.headers["content-type"] == "application/problem+json"
    assert too_big.json()["code"] == "platform.payload_too_large"
    assert too_big.json()["request_id"] == too_big.headers["x-request-id"]
    assert too_big.headers["x-content-type-options"] == "nosniff"
    assert at_the_cap.status_code == 200 and at_the_cap.json() == {"size": MAX_BODY_BYTES}


@pytest.mark.parametrize("path", ["/v1/_test/raw", "/v1/_test/profile"])
def test_a_streamed_body_is_counted_while_it_arrives(raw_client: TestClient, path: str) -> None:
    """A chunked body declares no length; FastAPI's own body parsing (`/profile`) and a route
    reading `request.body()` (`/raw`, like the webhooks) both answer 413, not 400."""
    too_big = raw_client.post(
        path, content=_chunks(MAX_BODY_BYTES + 1), headers={"Content-Type": "application/json"}
    )

    assert too_big.request.headers.get("transfer-encoding") == "chunked"
    assert "content-length" not in too_big.request.headers
    assert too_big.status_code == 413, too_big.text
    assert too_big.json()["code"] == "platform.payload_too_large"
    assert too_big.headers["content-type"] == "application/problem+json"


def test_a_small_streamed_body_still_arrives_whole(raw_client: TestClient) -> None:
    answer = raw_client.post("/v1/_test/raw", content=_chunks(300 * 1024))

    assert answer.status_code == 200 and answer.json() == {"size": 300 * 1024}


def test_the_operator_endpoints_take_the_training_jobs_larger_batches(
    raw_client: TestClient,
) -> None:
    """The feature snapshots of 5,000 households are about 2.7 MB (body_limit's docstring)."""
    assert body_limit.limit_for("/v1/internal/intel/feature-snapshots") == MAX_INTERNAL_BODY_BYTES
    assert body_limit.limit_for("/v1/internal") == MAX_BODY_BYTES  # the prefix, not a lookalike
    assert body_limit.limit_for("/v1/internalx/jobs") == MAX_BODY_BYTES

    # No JOBS_TOKEN locally, so the request reaches the route; an unknown job is a 404.
    accepted = raw_client.post("/v1/internal/jobs/no_such_job", content=_chunks(3 * 1024 * 1024))
    refused = raw_client.post(
        "/v1/internal/jobs/no_such_job", content=b"x" * (MAX_INTERNAL_BODY_BYTES + 1)
    )

    assert accepted.status_code != 413
    assert refused.status_code == 413
