"""Fuzz every endpoint from the OpenAPI contract (schemathesis): no 500s, answers match the schema.

The fuzzer calls the app from its own event loop, so it cannot share a test's rolled-back session.
It gets its own throwaway database instead (created, migrated and dropped here), so nothing it
writes reaches the other tests.

The operations are split into SHARDS tests by a stable hash of "METHOD /path", so pytest-xdist runs
them on several workers at once: one test for all of them took ten minutes by itself, the floor of
the whole run. Every operation is still fuzzed with the same examples and checks. Each shard gets
its own copy of one migrated, seeded database, so what an operation writes reaches only the
operations of its shard, whichever worker runs it.
"""

import asyncio
import zlib
from collections.abc import Callable, Iterator

import pytest
import schemathesis
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, settings
from schemathesis.checks import (
    allow_header_conformance,
    negative_data_rejection,
    positive_data_acceptance,
)
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import create_engine
from gharbasket.tools.seed import seed_societies

from .conftest import alembic_config, make_settings, run_on_server

pytestmark = pytest.mark.services

FUZZ_PHONE = "+919800009999"


def _seed(database_url: str) -> None:
    async def run() -> None:
        engine = create_engine(database_url, pooled=False)
        try:
            async with AsyncSession(engine) as session:
                await seed_societies(session)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(run())


@pytest.fixture(scope="module")
def fuzz_template(database_url: str) -> Iterator[str]:
    """A database migrated and seeded once per worker, for each shard to copy."""
    # Named after the test database, so runs against different test databases (parallel
    # worktrees, reviews) never drop each other's fuzz databases.
    template = f"{make_url(database_url).database}_fuzz_template"
    url = make_url(database_url).set(database=template).render_as_string(hide_password=False)
    run_on_server(database_url, f"DROP DATABASE IF EXISTS {template} WITH (FORCE)")
    run_on_server(database_url, f"CREATE DATABASE {template}")
    try:
        command.upgrade(alembic_config(url), "head")
        _seed(url)  # societies, so onboarding and search have something real to hit
        yield template
    finally:
        run_on_server(database_url, f"DROP DATABASE IF EXISTS {template} WITH (FORCE)")


@pytest.fixture
def fuzz_database_url(database_url: str, fuzz_template: str) -> Iterator[str]:
    fuzz_database = f"{make_url(database_url).database}_fuzz"
    url = make_url(database_url).set(database=fuzz_database).render_as_string(hide_password=False)
    run_on_server(database_url, f"DROP DATABASE IF EXISTS {fuzz_database} WITH (FORCE)")
    run_on_server(database_url, f"CREATE DATABASE {fuzz_database} TEMPLATE {fuzz_template}")
    try:
        yield url
    finally:
        run_on_server(database_url, f"DROP DATABASE IF EXISTS {fuzz_database} WITH (FORCE)")


@pytest.fixture
def fuzz_app(fuzz_database_url: str) -> FastAPI:
    # APP_ENV=dev: the fuzzer signs in with dev_phone. No Valkey: rate limits stay out of the way.
    # Webhook secrets, so fuzzed webhooks reach the signature and verify-token checks (401, 403)
    # instead of the "not configured" 503 a server error check would flag. Storage settings for
    # the same reason: presigning signs locally and never reaches the (absent) endpoint.
    settings = make_settings(
        app_env="dev",
        database_url=fuzz_database_url,
        razorpay_webhook_secret="fuzz-webhook-secret",
        whatsapp_app_secret="fuzz-whatsapp-secret",
        whatsapp_verify_token="fuzz-verify-token",
        s3_endpoint_url="http://127.0.0.1:9",
        s3_access_key="fuzz",
        s3_secret_key="fuzz-storage-secret",
    )
    return create_app(settings)


@pytest.fixture
def fuzz_headers(fuzz_app: FastAPI) -> dict[str, str]:
    with TestClient(fuzz_app) as client:
        signed_in = client.post(
            "/v1/auth/exchange", json={"dev_phone": FUZZ_PHONE, "device": {"platform": "android"}}
        )
    assert signed_in.status_code == 200, signed_in.text
    return {"Authorization": f"Bearer {signed_in.json()['access_token']}"}


@pytest.fixture
def api_schema(fuzz_app: FastAPI) -> schemathesis.BaseSchema:
    return schemathesis.openapi.from_asgi("/v1/openapi.json", fuzz_app)


# Left out: sign-in and sign-out (fuzzing them would sign the fuzzer itself out; tests/identity
# covers them) and the operator endpoint /v1/ready (open in dev, token-protected when deployed).
schema = schemathesis.pytest.from_fixture("api_schema").exclude(
    path_regex=r"^/v1/(auth/|ready$|internal/)"
)

SKIPPED_CHECKS = [
    # "Valid by the schema" data still breaks business rules (unknown society, Jain needs veg):
    # those 4xx answers are right, so this check would only report noise.
    positive_data_acceptance,
    # Starlette's 405 lists the methods of the matched route only, not of the whole path.
    allow_header_conformance,
    # Flaky false positives: it calls `q=3.9e16` "not a string" (query values are always text)
    # and expects a rejection for header values the real token replaces. Explicit tests cover
    # the real cases instead (e.g. consent `granted` must be a JSON boolean).
    negative_data_rejection,
]


SHARDS = 8


def _shard_of(index: int) -> Callable[[object], bool]:
    def in_shard(context: object) -> bool:
        label: str = context.operation.label  # pyright: ignore[reportAttributeAccessIssue]
        return zlib.crc32(label.encode()) % SHARDS == index

    return in_shard


def _fuzz_shard(index: int) -> Callable[..., None]:
    @schema.include(func=_shard_of(index)).parametrize()
    @settings(
        max_examples=25,
        deadline=None,
        # Same examples on every run, so CI never flakes; new endpoints get new examples.
        derandomize=True,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    def test_every_endpoint_survives_fuzzing(
        case: schemathesis.Case, fuzz_headers: dict[str, str]
    ) -> None:
        """No 500s; every answer is documented and shaped as the schema says."""
        case.call_and_validate(headers=fuzz_headers, excluded_checks=SKIPPED_CHECKS)

    test_every_endpoint_survives_fuzzing.__name__ = f"test_every_endpoint_survives_fuzzing_{index}"
    test_every_endpoint_survives_fuzzing.__qualname__ = (
        test_every_endpoint_survives_fuzzing.__name__
    )
    return test_every_endpoint_survives_fuzzing


for _index in range(SHARDS):
    globals()[f"test_every_endpoint_survives_fuzzing_{_index}"] = _fuzz_shard(_index)
