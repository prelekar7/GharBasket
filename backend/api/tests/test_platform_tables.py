"""Outbox, Idempotency-Key, audit log and cursor pagination (M0 US-5, US-7)."""

import asyncio
import uuid
from typing import Any

import httpx2
import pytest
import structlog
from fastapi import FastAPI
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform.audit import record
from gharbasket.platform.db import DbSession, create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.models import AuditEntry, IdempotencyKey, OutboxEvent
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

from .conftest import make_settings

pytestmark = pytest.mark.anyio


class TopupIn(BaseModel):
    amount_paise: int
    note: str = ""


class TopupOut(BaseModel):
    event_id: str
    amount_paise: int


def add_topup_route(app: FastAPI, *, pause_s: float = 0.0) -> None:
    """A stand-in for a real money endpoint: one side effect (an outbox event) per real run."""

    @app.post("/v1/_test/topups", status_code=201)
    async def create_topup(body: TopupIn, session: DbSession, idem: Idempotent) -> TopupOut:
        event_id = await publish(
            session, "billing.wallet_topped_up", {"amount_paise": body.amount_paise}
        )
        if body.amount_paise == 0:
            raise ProblemError(409, "billing.test_refused", "Refused after the side effect.")
        await asyncio.sleep(pause_s)  # widens the window for the concurrency test
        result = TopupOut(event_id=str(event_id), amount_paise=body.amount_paise)
        await idem.save(201, result)
        await session.commit()
        return result


@pytest.fixture
def api_app(api: httpx2.AsyncClient) -> httpx2.AsyncClient:
    transport = api._transport  # pyright: ignore[reportPrivateUsage]
    assert isinstance(transport, httpx2.ASGITransport)
    app = transport.app
    assert isinstance(app, FastAPI)
    add_topup_route(app)
    return api


async def _count(session: AsyncSession, model: type[Any]) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


# --- outbox -----------------------------------------------------------------------------------


@pytest.mark.services
async def test_publish_writes_an_event_with_a_time_ordered_id(db_session: AsyncSession) -> None:
    event_id = await publish(db_session, "orders.locked", {"order_id": "o-1"})

    event = await db_session.get(OutboxEvent, event_id)
    assert event is not None
    assert event.id.version == 7
    assert event.payload == {"order_id": "o-1"}
    assert event.sent_at is None
    assert event.attempts == 0


@pytest.mark.services
@pytest.mark.parametrize("bad", ["locked", "Orders.locked", "orders.", "orders.locked.now", ""])
async def test_publish_rejects_badly_named_events(db_session: AsyncSession, bad: str) -> None:
    with pytest.raises(ValueError, match=r"module\.event"):
        await publish(db_session, bad, {})


@pytest.mark.services
async def test_an_event_disappears_with_its_rolled_back_transaction(database_url: str) -> None:
    engine = create_engine(database_url, pooled=False)
    marker = uuid.uuid4().hex
    try:
        async with AsyncSession(engine) as session:
            await publish(session, "orders.locked", {"marker": marker})
            await session.rollback()
        async with AsyncSession(engine) as session:
            found = await session.execute(
                select(func.count()).where(OutboxEvent.payload["marker"].astext == marker)
            )
            assert found.scalar_one() == 0
    finally:
        await engine.dispose()


# --- Idempotency-Key --------------------------------------------------------------------------


@pytest.mark.services
async def test_a_retry_replays_the_first_answer_without_running_twice(
    api_app: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    headers = {"Idempotency-Key": "topup-1"}
    first = await api_app.post(
        "/v1/_test/topups", json={"amount_paise": 50000, "note": ""}, headers=headers
    )
    # Same request, different JSON formatting and key order: still the same request.
    retry = await api_app.post(
        "/v1/_test/topups",
        content=b'{ "note": "",  "amount_paise": 50000 }',
        headers={**headers, "Content-Type": "application/json"},
    )

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["idempotent-replayed"] == "true"
    assert "idempotent-replayed" not in first.headers
    assert await _count(db_session, OutboxEvent) == 1


@pytest.mark.services
async def test_reusing_a_key_for_a_different_request_is_refused(
    api_app: httpx2.AsyncClient,
) -> None:
    headers = {"Idempotency-Key": "topup-2"}
    await api_app.post("/v1/_test/topups", json={"amount_paise": 100}, headers=headers)
    other = await api_app.post("/v1/_test/topups", json={"amount_paise": 999}, headers=headers)

    assert other.status_code == 422
    assert other.json()["code"] == "request.idempotency_mismatch"


@pytest.mark.services
@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, "request.idempotency_key_required"),
        ({"Idempotency-Key": "x" * 256}, "request.idempotency_key_invalid"),
        ({"Idempotency-Key": "नमस्ते".encode()}, "request.idempotency_key_invalid"),
    ],
)
async def test_the_key_is_required_and_checked(
    api_app: httpx2.AsyncClient, headers: dict[str, Any], code: str
) -> None:
    response = await api_app.post("/v1/_test/topups", json={"amount_paise": 1}, headers=headers)

    assert response.status_code == 400
    assert response.json()["code"] == code


@pytest.mark.services
async def test_a_failed_request_leaves_the_key_free_for_a_retry(
    api_app: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    headers = {"Idempotency-Key": "topup-3"}
    failed = await api_app.post("/v1/_test/topups", json={"amount_paise": 0}, headers=headers)

    assert failed.status_code == 409
    assert await _count(db_session, IdempotencyKey) == 0
    assert await _count(db_session, OutboxEvent) == 0


@pytest.mark.services
async def test_an_expired_key_can_be_used_again(
    api_app: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    headers = {"Idempotency-Key": "topup-4"}
    await api_app.post("/v1/_test/topups", json={"amount_paise": 100}, headers=headers)
    await db_session.execute(
        update(IdempotencyKey).values(created_at=func.now() - text("interval '25 hours'"))
    )

    reused = await api_app.post("/v1/_test/topups", json={"amount_paise": 777}, headers=headers)

    assert reused.status_code == 201
    assert reused.json()["amount_paise"] == 777
    assert "idempotent-replayed" not in reused.headers


@pytest.mark.services
async def test_two_copies_at_the_same_moment_run_once(database_url: str) -> None:
    """Real, committed transactions: the second request waits for the first, then replays it."""
    app = create_app(make_settings(database_url=database_url))
    add_topup_route(app, pause_s=0.2)
    key = f"race-{uuid.uuid4().hex}"
    headers = {"Idempotency-Key": key}

    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
            one, two = await asyncio.gather(
                client.post("/v1/_test/topups", json={"amount_paise": 4200}, headers=headers),
                client.post("/v1/_test/topups", json={"amount_paise": 4200}, headers=headers),
            )

    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine) as session:
            event_id = uuid.UUID(one.json()["event_id"])
            events = await session.execute(
                select(func.count()).where(OutboxEvent.payload["amount_paise"].as_integer() == 4200)
            )
            assert one.status_code == two.status_code == 201
            assert one.json() == two.json()
            assert sum("idempotent-replayed" in r.headers for r in (one, two)) == 1
            assert events.scalar_one() == 1
            await session.execute(delete(OutboxEvent).where(OutboxEvent.id == event_id))
            await session.execute(delete(IdempotencyKey).where(IdempotencyKey.key == key))
            await session.commit()
    finally:
        await engine.dispose()


# --- audit log --------------------------------------------------------------------------------


@pytest.mark.services
async def test_audit_entries_carry_the_request_id(db_session: AsyncSession) -> None:
    actor = uuid.uuid4()
    structlog.contextvars.bind_contextvars(request_id="req-audit-1")
    try:
        await record(
            db_session,
            actor_user_id=actor,
            action="roles.granted",
            entity_type="user",
            entity_id="u-1",
            after={"role": "hub_manager"},
        )
    finally:
        structlog.contextvars.clear_contextvars()

    entry = (await db_session.execute(select(AuditEntry))).scalar_one()
    assert entry.actor_user_id == actor
    assert entry.request_id == "req-audit-1"
    assert entry.after == {"role": "hub_manager"}


# --- cursor pagination ------------------------------------------------------------------------


def test_cursor_round_trip() -> None:
    position = {"created_at": "2026-10-03T10:00:00+00:00", "id": "0199a1b2"}

    cursor = encode_cursor(position)

    assert "=" not in cursor
    assert decode_cursor(cursor) == position


@pytest.mark.parametrize("bad", ["not-base64!!", encode_cursor({}).replace("e", "%"), "WzEsMl0"])
def test_a_tampered_cursor_is_a_422(bad: str) -> None:
    with pytest.raises(ProblemError) as caught:
        decode_cursor(bad)

    assert caught.value.status == 422
    assert caught.value.code == "request.invalid_cursor"
