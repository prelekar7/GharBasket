"""WhatsApp (spec §3.2): sending approved templates through a fake Cloud API, and Meta's webhook
(handshake, signature, each status update once, receipts applied to their delivery)."""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Consent, User
from gharbasket.modules.notifications.fcm import Failed, Retry, Sent
from gharbasket.modules.notifications.models import (
    Delivery,
    Message,
    Preference,
    Template,
    WebhookEvent,
)
from gharbasket.modules.notifications.whatsapp import WhatsAppClient
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import JobRun

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from .conftest import PRIYA, Home
from .test_messages import NOON, deliver, messages_of
from .test_send import locked

pytestmark = [pytest.mark.anyio, pytest.mark.services]

WHATSAPP = {
    "whatsapp_token": "meta-access-token",
    "whatsapp_phone_number_id": "106540352242922",
    "whatsapp_app_secret": "meta-app-secret",
    "whatsapp_verify_token": "meta-verify-token",
}
SEND_URL = "https://graph.facebook.com/v23.0/106540352242922/messages"
HOOK = "/v1/webhooks/whatsapp"


class FakeMeta:
    """The Cloud API's send endpoint: answers `answer` (a wamid by default) and keeps each body."""

    def __init__(self) -> None:
        self.sends: list[dict[str, Any]] = []
        self.answer = httpx2.Response(200, json={"messages": [{"id": "wamid.ONE"}]})
        self.http = httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle))

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == SEND_URL
        assert request.headers["authorization"] == "Bearer meta-access-token"
        self.sends.append(json.loads(request.content))
        return self.answer


def meta_error(status: int, code: int) -> httpx2.Response:
    return httpx2.Response(status, json={"error": {"message": "no", "code": code}})


async def send(db: AsyncSession, meta: FakeMeta, enabled: bool = True) -> dict[str, Any]:
    settings = make_settings(**(WHATSAPP if enabled else {}))
    run: JobRun = await run_job(db, "send_deliveries", settings=settings, http=meta.http)
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


async def wants_whatsapp(db: AsyncSession, user_id: uuid.UUID, locale: str = "en-IN") -> None:
    db.add(Preference(user_id=user_id, category="money", push=False, whatsapp=True))
    db.add(Consent(user_id=user_id, purpose="whatsapp", version="v1"))
    await db.execute(update(User).where(User.id == user_id).values(locale=locale))
    for loc, body in (("en-IN", "Box for {day} confirmed: {total}."), ("hi-IN", "{total}: {day}")):
        db.add(
            Template(
                kind="orders.locked",
                channel="whatsapp",
                locale=loc,
                version=1,
                title="",
                body=body,
                wa_template_name="box_confirmed",
                active=True,
            )
        )
    await db.flush()


async def whatsapp_of(db: AsyncSession, event_id: uuid.UUID) -> Delivery:
    (message,) = await messages_of(db, event_id)
    found = await db.scalar(
        select(Delivery).where(Delivery.message_id == message.id, Delivery.channel == "whatsapp")
    )
    assert found is not None
    await db.refresh(found)
    return found


@time_machine.travel(NOON, tick=False)
async def test_an_approved_template_goes_to_the_persons_phone_in_their_language(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya, meta = homes[PRIYA], FakeMeta()
    await wants_whatsapp(db_session, priya.user_id, locale="hi-IN")
    event_id = await deliver(db_session, "orders.locked", locked(priya))

    assert (await send(db_session, meta))["sent"] == 1

    assert meta.sends == [
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": PRIYA.removeprefix("+"),
            "type": "template",
            "template": {
                "name": "box_confirmed",
                "language": {"code": "hi"},
                "components": [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": "₹690"},
                            {"type": "text", "text": "मंगलवार"},
                        ],
                    }
                ],
            },
        }
    ]
    delivery = await whatsapp_of(db_session, event_id)
    assert (delivery.status, delivery.provider_ref, delivery.sent_at) == ("sent", "wamid.ONE", NOON)


@time_machine.travel(NOON, tick=False)
async def test_whatsapp_is_skipped_until_set_up_and_without_consent_or_a_template(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya, meta = homes[PRIYA], FakeMeta()
    await wants_whatsapp(db_session, priya.user_id)
    off = await deliver(db_session, "orders.locked", locked(priya))
    assert (await send(db_session, meta, enabled=False))["skipped"] == 1
    assert (await whatsapp_of(db_session, off)).last_error == "WhatsApp is not set up (WHATSAPP_*)"

    no_template = await deliver(db_session, "orders.locked", locked(priya))
    await db_session.execute(
        update(Template).where(Template.channel == "whatsapp").values(active=False)
    )
    assert (await send(db_session, meta))["skipped"] == 1
    assert (await whatsapp_of(db_session, no_template)).last_error == (
        "no approved WhatsApp template for orders.locked"
    )

    revoked = await deliver(db_session, "orders.locked", locked(priya))
    await db_session.execute(
        update(Consent).where(Consent.user_id == priya.user_id).values(revoked_at=NOON)
    )
    assert (await send(db_session, meta))["skipped"] == 1
    assert (await whatsapp_of(db_session, revoked)).last_error == "no WhatsApp consent"
    assert meta.sends == []


@time_machine.travel(NOON, tick=False)
async def test_meta_refusals_are_retried_or_failed_like_fcm(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya, meta = homes[PRIYA], FakeMeta()
    await wants_whatsapp(db_session, priya.user_id)
    meta.answer = meta_error(400, 130429)  # throughput limit
    throttled = await deliver(db_session, "orders.locked", locked(priya))
    assert (await send(db_session, meta))["retrying"] == 1
    delivery = await whatsapp_of(db_session, throttled)
    assert (delivery.status, delivery.attempts, delivery.last_error) == (
        "queued",
        1,
        "HTTP 400 code 130429",
    )

    meta.answer = meta_error(400, 132001)  # the template does not exist in that language
    refused = await deliver(db_session, "orders.locked", locked(priya))
    assert (await send(db_session, meta))["failed"] == 1
    assert (await whatsapp_of(db_session, refused)).last_error == "HTTP 400 code 132001"


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        (httpx2.Response(200, text="not json"), Sent("")),
        (httpx2.Response(503, text="<html>"), Retry("HTTP 503")),
        (meta_error(400, 131056), Retry("HTTP 400 code 131056")),
        (meta_error(401, 190), Failed("HTTP 401 code 190")),
        (httpx2.ConnectError("down"), Retry("ConnectError")),
    ],
)
async def test_cloud_api_answers_become_outcomes(
    answer: httpx2.Response | Exception, outcome: Any
) -> None:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    http = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    client = WhatsAppClient(http, "token", "106540352242922")
    assert await client.send("+919800001001", "box_confirmed", "mr-IN", []) == outcome


# --- the webhook ---------------------------------------------------------------------------------


def signed(body: bytes, secret: str = "meta-app-secret") -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"X-Hub-Signature-256": f"sha256={digest}", "Content-Type": "application/json"}


def receipts(*statuses: dict[str, Any]) -> bytes:
    change = {"field": "messages", "value": {"messaging_product": "whatsapp", "statuses": statuses}}
    body = {"object": "whatsapp_business_account", "entry": [{"id": "1", "changes": [change]}]}
    return json.dumps(body).encode()


def status(wamid: str, state: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": wamid,
        "status": state,
        "timestamp": "1760270000",
        "recipient_id": "91980",
        **extra,
    }


async def test_meta_verifies_the_webhook_with_its_token(clients: ClientFactory) -> None:
    api = clients(**WHATSAPP)
    query = {
        "hub.mode": "subscribe",
        "hub.verify_token": "meta-verify-token",
        "hub.challenge": "42",
    }

    ok = await api.get(HOOK, params=query)
    assert (ok.status_code, ok.text, ok.headers["content-type"]) == (
        200,
        "42",
        "text/plain; charset=utf-8",
    )
    wrong = await api.get(HOOK, params={**query, "hub.verify_token": "guess"})
    assert (wrong.status_code, wrong.json()["code"]) == (403, "notifications.bad_verify_token")
    unset = await clients().get(HOOK, params=query)
    assert (unset.status_code, unset.json()["code"]) == (503, "notifications.whatsapp_unavailable")


async def test_receipts_move_their_delivery_forward_once_each(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    message = Message(
        user_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        event_type="orders.delivered",
        kind="orders.delivered",
        category="delivery",
        params={},
        link="/orders",
    )
    db_session.add(message)
    await db_session.flush()
    read = Delivery(
        message_id=message.id, channel="whatsapp", status="sent", provider_ref="wamid.A"
    )
    db_session.add(read)
    await db_session.flush()
    await db_session.commit()
    api = clients(**WHATSAPP)

    async def post(*statuses: dict[str, Any]) -> list[str]:
        body = receipts(*statuses)
        answer = await api.post(HOOK, content=body, headers=signed(body))
        assert answer.status_code == 200, answer.text
        return answer.json()["outcomes"]

    assert await post(status("wamid.A", "delivered"), status("wamid.Z", "read")) == [
        "applied",
        "unknown_delivery",
    ]
    assert await post(status("wamid.A", "delivered")) == ["duplicate"]
    assert await post(status("wamid.A", "read"), status("wamid.A", "sent")) == [
        "applied",
        "unchanged",
    ]
    error = {"code": 131026, "title": "Message undeliverable"}
    assert await post(status("wamid.A", "failed", errors=[error])) == ["unchanged"]
    await db_session.refresh(read)
    assert read.status == "read"

    read.provider_ref = "wamid.B"
    read.status = "sent"
    await db_session.commit()
    assert await post(status("wamid.B", "failed", errors=[error])) == ["applied"]
    await db_session.refresh(read)
    assert (read.status, read.last_error) == ("failed", "WhatsApp 131026 Message undeliverable")
    events = await db_session.scalar(select(func.count()).select_from(WebhookEvent))
    assert events == 6
    customer = {"value": {"messages": [{"from": "91980", "text": {"body": "hi"}}]}}
    incoming = json.dumps(
        {"object": "whatsapp_business_account", "entry": [{"changes": [customer]}]}
    ).encode()
    answer = await api.post(HOOK, content=incoming, headers=signed(incoming))
    assert answer.json() == {"outcomes": []}


async def test_an_unsigned_or_unreadable_webhook_is_refused(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    api = clients(**WHATSAPP)
    body = receipts(status("wamid.A", "delivered"))

    forged = await api.post(HOOK, content=body, headers=signed(body, "another-secret"))
    assert (forged.status_code, forged.json()["code"]) == (401, "notifications.bad_signature")
    bare = await api.post(HOOK, content=body, headers={"X-Hub-Signature-256": "sha256=00"})
    assert bare.status_code == 401
    for junk in (b"not json", b"[1, 2]"):
        answer = await api.post(HOOK, content=junk, headers=signed(junk))
        assert (answer.status_code, answer.json()["code"]) == (400, "request.invalid")
    unset = await clients().post(HOOK, content=body, headers=signed(body))
    assert (unset.status_code, unset.json()["code"]) == (503, "notifications.whatsapp_unavailable")
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 0


async def test_webhook_events_are_purged_with_the_messages(db_session: AsyncSession) -> None:
    db_session.add(
        WebhookEvent(provider="whatsapp", provider_event_id="wamid.OLD:read", payload={})
    )
    await db_session.flush()
    later = datetime.now(UTC) + timedelta(days=181)
    run = await run_job(
        db_session, "purge_notifications", settings=make_settings(), scheduled_for=later
    )
    assert run.status == "ok"
    assert await db_session.scalar(select(func.count()).select_from(WebhookEvent)) == 0
