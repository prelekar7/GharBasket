"""Fuzz every endpoint from the OpenAPI contract (schemathesis): no 500s, answers match the schema.

The fuzzer calls the app from its own event loop, so it cannot share a test's rolled-back session.
It gets its own throwaway database instead (created, migrated and dropped here), so nothing it
writes reaches the other tests.
"""

import asyncio
from collections.abc import Iterator

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
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.db import create_engine
from gharbasket.tools.seed import seed_societies

from .conftest import alembic_config, make_settings

pytestmark = pytest.mark.services

FUZZ_PHONE = "+919800009999"


def _admin(sql: str, database_url: str) -> None:
    async def run() -> None:
        engine = create_engine(database_url, pooled=False)
        try:
            async with engine.connect() as connection:
                await connection.execution_options(isolation_level="AUTOCOMMIT")
                await connection.execute(text(sql))
        finally:
            await engine.dispose()

    asyncio.run(run())


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
def fuzz_database_url(database_url: str) -> Iterator[str]:
    # Named after the test database, so runs against different test databases (parallel
    # worktrees, reviews) never drop each other's fuzz database.
    fuzz_database = f"{make_url(database_url).database}_fuzz"
    url = make_url(database_url).set(database=fuzz_database).render_as_string(hide_password=False)
    _admin(f"DROP DATABASE IF EXISTS {fuzz_database} WITH (FORCE)", database_url)
    _admin(f"CREATE DATABASE {fuzz_database}", database_url)
    try:
        command.upgrade(alembic_config(url), "head")
        _seed(url)  # societies, so onboarding and search have something real to hit
        yield url
    finally:
        _admin(f"DROP DATABASE IF EXISTS {fuzz_database} WITH (FORCE)", database_url)


@pytest.fixture(scope="module")
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


@pytest.fixture(scope="module")
def fuzz_headers(fuzz_app: FastAPI) -> dict[str, str]:
    with TestClient(fuzz_app) as client:
        signed_in = client.post(
            "/v1/auth/exchange", json={"dev_phone": FUZZ_PHONE, "device": {"platform": "android"}}
        )
    assert signed_in.status_code == 200, signed_in.text
    return {"Authorization": f"Bearer {signed_in.json()['access_token']}"}


@pytest.fixture(scope="module")
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


@schema.parametrize()
@settings(
    max_examples=25,
    deadline=None,
    # Same examples on every run, so CI never flakes; new endpoints get new examples.
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)
def test_every_endpoint_survives_fuzzing(case: schemathesis.Case, fuzz_headers: dict[str, str]):
    """No 500s; every answer is documented and shaped as the schema says."""
    case.call_and_validate(headers=fuzz_headers, excluded_checks=SKIPPED_CHECKS)
