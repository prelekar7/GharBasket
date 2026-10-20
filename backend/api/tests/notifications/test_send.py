"""Sending (spec §6): `send_deliveries` with a fake FCM, the FCM client's answers, and the purge."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx2
import jwt
import pytest
import time_machine
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.notifications import api as notifications_api
from gharbasket.modules.notifications.fcm import Failed, FcmClient, Retry, Sent, Unregistered
from gharbasket.modules.notifications.models import Delivery, Message
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import JobRun

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import MEENA, PRIYA, TOKEN_URI, FakeFcm, Home, fcm_error
from .test_messages import LATE, NOON, SEVEN_AM, deliver, messages_of

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def sign_in_devices(
    clients: ClientFactory, google: FakeGoogle, phone: str, *tokens: str
) -> None:
    """One signed-in phone per FCM token."""
    api = clients()
    for token in tokens:
        answer = await api.post(
            "/v1/auth/exchange",
            json={
                "firebase_id_token": google.id_token(phone_number=phone, sub=f"uid{phone}"),
                "device": {"platform": "android", "push_token": token},
            },
        )
        assert answer.status_code == 200, answer.text


async def send(db: AsyncSession, fcm: FakeFcm, account: dict[str, Any] | None) -> dict[str, Any]:
    settings = make_settings(fcm_service_account=json.dumps(account) if account else None)
    run: JobRun = await run_job(db, "send_deliveries", settings=settings, http=fcm.http)
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


async def push_of(db: AsyncSession, event_id: uuid.UUID) -> Delivery:
    (message,) = await messages_of(db, event_id)
    found = await db.scalar(
        select(Delivery).where(Delivery.message_id == message.id, Delivery.channel == "push")
    )
    assert found is not None
    await db.refresh(found)
    return found


def locked(home: Home) -> dict[str, Any]:
    return {
        "household_id": str(home.household_id),
        "order_id": str(uuid.uuid4()),
        "delivery_date": "2026-10-13",
        "total_paise": 69_000,
    }


@time_machine.travel(NOON, tick=False)
async def test_a_due_push_goes_to_every_device_in_the_persons_language(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    homes: dict[str, Home],
    fcm: FakeFcm,
    service_account: dict[str, Any],
    rsa_key: rsa.RSAPrivateKey,
) -> None:
    priya = homes[PRIYA]
    await sign_in_devices(clients, google, PRIYA, "phone-a", "gone-b")
    await db_session.execute(update(User).where(User.id == priya.user_id).values(locale="hi-IN"))
    event_id = await deliver(db_session, "orders.locked", locked(priya))

    summary = await send(db_session, fcm, service_account)

    assert summary == {"sent": 1, "retrying": 0, "failed": 0, "skipped": 0}
    push = await push_of(db_session, event_id)
    assert (push.status, push.attempts, push.sent_at, push.last_error) == ("sent", 1, NOON, None)
    assert push.provider_ref == "projects/gharbasket-test/messages/phone-a"
    (message,) = await messages_of(db_session, event_id)
    assert sorted(s["token"] for s in fcm.sends) == ["gone-b", "phone-a"]
    assert {json.dumps(s, ensure_ascii=False, sort_keys=True) for s in fcm.sends} == {
        json.dumps(
            {
                "token": token,
                "notification": {
                    "title": "मंगलवार का बॉक्स पक्का",
                    "body": "आपके वॉलेट से ₹690 चुकाए गए।",
                },
                "data": {"link": "/orders", "message_id": str(message.id)},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        for token in ("phone-a", "gone-b")
    }
    # FCM no longer knows gone-b: M1 forgets it.
    targets = await identity_api.active_push_tokens(db_session, priya.user_id)
    assert [t.token for t in targets] == ["phone-a"]

    # The access token came from a JWT signed with the service account's key.
    (token_request,) = fcm.token_requests
    form = {k: v[0] for k, v in parse_qs(token_request.content.decode()).items()}
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:jwt-bearer"
    claims = jwt.decode(
        form["assertion"], rsa_key.public_key(), algorithms=["RS256"], audience=TOKEN_URI
    )
    issued = int(NOON.timestamp())
    assert claims == {
        "iss": service_account["client_email"],
        "sub": service_account["client_email"],
        "scope": "https://www.googleapis.com/auth/firebase.messaging",
        "aud": TOKEN_URI,
        "iat": issued,
        "exp": issued + 3600,
    }
    # … and is reused by the next run.
    await deliver(db_session, "orders.delivered", locked(priya))
    assert (await send(db_session, fcm, service_account))["sent"] == 1
    assert len(fcm.token_requests) == 1

    section = await notifications_api.export_for_user(db_session, priya.user_id)
    assert [m["deliveries"] for m in section["messages"]] == [{"push": "sent"}, {"push": "sent"}]


async def test_a_push_fcm_cannot_take_is_retried_after_1_5_and_30_minutes_then_fails(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    homes: dict[str, Home],
    fcm: FakeFcm,
    service_account: dict[str, Any],
) -> None:
    await sign_in_devices(clients, google, PRIYA, "flaky-phone")
    with time_machine.travel(NOON, tick=False):
        event_id = await deliver(db_session, "orders.locked", locked(homes[PRIYA]))

    expected = [(NOON, 1), (NOON + timedelta(minutes=1), 5), (NOON + timedelta(minutes=6), 30)]
    for at, wait in expected:
        with time_machine.travel(at, tick=False):
            assert (await send(db_session, fcm, service_account))["retrying"] == 1
            assert (await send(db_session, fcm, service_account))["retrying"] == 0  # not due
        push = await push_of(db_session, event_id)
        assert (push.status, push.next_attempt_at) == ("queued", at + timedelta(minutes=wait))
        assert push.last_error == "HTTP 503 UNAVAILABLE"

    with time_machine.travel(NOON + timedelta(minutes=36), tick=False):
        assert (await send(db_session, fcm, service_account))["failed"] == 1
    push = await push_of(db_session, event_id)
    assert (push.status, push.attempts, push.sent_at) == ("failed", 4, None)
    assert len(fcm.sends) == 4


async def test_a_held_push_goes_out_when_quiet_hours_end(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    homes: dict[str, Home],
    fcm: FakeFcm,
    service_account: dict[str, Any],
) -> None:
    await sign_in_devices(clients, google, PRIYA, "phone-a")
    order = locked(homes[PRIYA])
    with time_machine.travel(LATE, tick=False):
        held = await deliver(db_session, "orders.dispatched", order)
        critical = await deliver(db_session, "orders.at_society", order)
        assert (await send(db_session, fcm, service_account))["sent"] == 1
    assert (await push_of(db_session, critical)).status == "sent"
    assert (await push_of(db_session, held)).status == "held"

    with time_machine.travel(SEVEN_AM - timedelta(minutes=1), tick=False):
        assert (await send(db_session, fcm, service_account))["sent"] == 0
    with time_machine.travel(SEVEN_AM, tick=False):
        assert (await send(db_session, fcm, service_account))["sent"] == 1
    assert (await push_of(db_session, held)).sent_at == SEVEN_AM


@time_machine.travel(NOON, tick=False)
async def test_with_nowhere_to_send_a_delivery_is_skipped(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    homes: dict[str, Home],
    fcm: FakeFcm,
    service_account: dict[str, Any],
) -> None:
    priya, meena = homes[PRIYA], homes[MEENA]
    await sign_in_devices(clients, google, PRIYA, "bad-token")
    without_fcm = await deliver(db_session, "orders.locked", locked(priya))
    assert (await send(db_session, fcm, None))["skipped"] == 1
    assert (await push_of(db_session, without_fcm)).last_error == (
        "FCM is not set up (FCM_SERVICE_ACCOUNT)"
    )

    no_device = await deliver(db_session, "orders.locked", locked(meena))  # never signed in
    invalid = await deliver(db_session, "orders.locked", locked(priya))
    assert await send(db_session, fcm, service_account) == {
        "sent": 0,
        "retrying": 0,
        "failed": 0,
        "skipped": 2,
    }
    assert (await push_of(db_session, no_device)).last_error == (
        "no signed-in device with a push token"
    )
    assert (await push_of(db_session, invalid)).attempts == 1
    assert await identity_api.active_push_tokens(db_session, priya.user_id) == []

    (message,) = await messages_of(db_session, invalid)
    db_session.add(
        Delivery(message_id=message.id, channel="whatsapp", status="queued", next_attempt_at=NOON)
    )
    await db_session.flush()
    assert (await send(db_session, fcm, service_account))["skipped"] == 1
    section = await notifications_api.export_for_user(db_session, priya.user_id)
    assert section["messages"][-1]["deliveries"] == {"push": "skipped", "whatsapp": "skipped"}


async def test_the_purge_deletes_messages_after_180_days_with_their_deliveries(
    db_session: AsyncSession,
) -> None:
    user_id = uuid.uuid4()
    now = datetime(2026, 10, 9, 23, 3, tzinfo=UTC)
    kept, purged = (
        Message(
            user_id=user_id,
            event_id=uuid.uuid4(),
            event_type="orders.delivered",
            kind="orders.delivered",
            category="delivery",
            params={},
            link="/orders",
            created_at=now - timedelta(days=days),
        )
        for days in (179, 181)
    )
    db_session.add_all([kept, purged])
    await db_session.flush()
    db_session.add(Delivery(message_id=purged.id, channel="push", status="sent"))
    await db_session.flush()

    settings = make_settings()
    run = await run_job(db_session, "purge_notifications", settings=settings, scheduled_for=now)

    assert (run.status, run.summary) == ("ok", {"deleted": 1})
    db_session.expunge_all()
    left = (await db_session.execute(select(Message.id).where(Message.user_id == user_id))).all()
    assert [row.id for row in left] == [kept.id]
    assert (await db_session.execute(select(Delivery))).scalars().all() == []


# --- the FCM client's answers --------------------------------------------------------------------


def client(service_account: dict[str, Any], handle: Any) -> FcmClient:
    return FcmClient(httpx2.AsyncClient(transport=httpx2.MockTransport(handle)), service_account)


def sends_with(response: httpx2.Response | Exception) -> Any:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if str(request.url) == TOKEN_URI:
            return httpx2.Response(200, json={"access_token": "ya29.fake", "expires_in": 3599})
        if isinstance(response, Exception):
            raise response
        return response

    return handle


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (httpx2.Response(200, text="not json"), Sent("")),
        (fcm_error(404, "NOT_FOUND", "UNREGISTERED", "gone"), Unregistered()),
        (fcm_error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "Bad token"), Unregistered()),
        (
            fcm_error(400, "INVALID_ARGUMENT", "INVALID_ARGUMENT", "Bad data key"),
            Failed("HTTP 400 INVALID_ARGUMENT"),
        ),
        (
            fcm_error(403, "PERMISSION_DENIED", "SENDER_ID_MISMATCH", "no"),
            Failed("HTTP 403 SENDER_ID_MISMATCH"),
        ),
        (
            httpx2.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}}),
            Retry("HTTP 429 RESOURCE_EXHAUSTED"),
        ),
        (httpx2.Response(502, text="<html>"), Retry("HTTP 502")),
        (httpx2.ConnectError("down"), Retry("ConnectError")),
    ],
)
async def test_fcm_answers_become_outcomes(
    answer: httpx2.Response | Exception, outcome: Any, service_account: dict[str, Any]
) -> None:
    fcm = client(service_account, sends_with(answer))
    assert await fcm.send("device", "Title", "Body", {}) == outcome


async def test_a_revoked_access_token_is_dropped_and_token_failures_are_told_apart(
    service_account: dict[str, Any],
) -> None:
    calls: list[str] = []

    def unauthorised(request: httpx2.Request) -> httpx2.Response:
        calls.append(str(request.url))
        if str(request.url) == TOKEN_URI:
            return httpx2.Response(200, json={"access_token": "ya29.fake"})
        return httpx2.Response(401, json={"error": {"status": "UNAUTHENTICATED"}})

    fcm = client(service_account, unauthorised)
    assert await fcm.send("device", "T", "B", {}) == Retry("HTTP 401")
    assert await fcm.send("device", "T", "B", {}) == Retry("HTTP 401")
    assert calls.count(TOKEN_URI) == 2  # a new access token after each 401

    def token_endpoint(status: int) -> Any:
        return lambda request: httpx2.Response(status, json={"error": "invalid_grant"})

    assert await client(service_account, token_endpoint(500)).send("d", "T", "B", {}) == Retry(
        "token endpoint: HTTP 500"
    )
    assert await client(service_account, token_endpoint(400)).send("d", "T", "B", {}) == Failed(
        "token endpoint: HTTP 400"
    )

    def down(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    assert await client(service_account, down).send("d", "T", "B", {}) == Retry(
        "token endpoint: ConnectError"
    )
    broken = {**service_account, "client_email": "other@test", "private_key": "not a key"}
    assert isinstance(await client(broken, down).send("d", "T", "B", {}), Failed)


def test_a_broken_service_account_stops_the_app_at_start() -> None:
    for broken in ("{not json", json.dumps({"client_email": "x"}), "[]"):
        with pytest.raises(ValidationError, match="FCM_SERVICE_ACCOUNT"):
            make_settings(fcm_service_account=broken)
    assert make_settings().fcm_enabled is False
    with pytest.raises(ValueError, match="not set"):
        _ = make_settings().fcm_credentials
