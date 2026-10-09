"""The inbox and preferences (spec §3.1)."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.notifications import api as notifications_api
from gharbasket.modules.notifications.kinds import KINDS
from gharbasket.modules.notifications.models import Message

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

INBOX = "/v1/notifications"
PREFS = "/v1/notifications/preferences"


async def person(
    clients: ClientFactory, google: FakeGoogle, phone: str
) -> tuple[httpx2.AsyncClient, dict[str, str], uuid.UUID]:
    api = clients()
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return api, bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def deliver(db: AsyncSession, user_id: uuid.UUID, kind: str, **params: Any) -> Message:
    """What the send pipeline's consumer will write for one event (B3)."""
    spec = KINDS[kind]
    message = Message(
        user_id=user_id,
        event_id=uuid.uuid4(),
        event_type=spec.event_type,
        kind=kind,
        category=spec.category,
        params=params,
        link=spec.link,
    )
    db.add(message)
    await db.flush()
    return message


async def test_the_inbox_is_rendered_in_the_request_language_newest_first_and_paged(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, auth, me = await person(clients, google, "+919800011101")
    _, _, someone = await person(clients, google, "+919800011102")
    empty = await api.get(INBOX, headers=auth)
    assert empty.json() == {"items": [], "unread": 0, "next_cursor": None}
    await deliver(db_session, me, "billing.wallet_topped_up", amount=200_000)
    await deliver(db_session, me, "orders.locked", total=69_000, day="2026-10-13")
    await deliver(db_session, me, "orders.at_society")
    await deliver(db_session, someone, "orders.delivered")

    first = await api.get(f"{INBOX}?limit=2", headers=auth)
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.headers["content-language"] == "en"
    page = first.json()
    assert page["unread"] == 3  # the whole inbox, not the page
    at_society, locked = page["items"]
    assert (at_society["event_type"], at_society["category"], at_society["link"]) == (
        "orders.at_society",
        "delivery",
        "/orders",
    )
    assert at_society["title"] == "Your box has reached your society"
    assert (locked["title"], locked["body"]) == (
        "Your box for Tuesday is confirmed",
        "₹690 was paid from your wallet.",
    )
    assert at_society["read_at"] is None

    rest = await api.get(f"{INBOX}?limit=2&cursor={page['next_cursor']}", headers=auth)
    (topped_up,) = rest.json()["items"]
    assert topped_up["title"] == "₹2,000 added to your wallet"
    assert rest.json()["next_cursor"] is None

    hindi = await api.get(INBOX, headers={**auth, "Accept-Language": "hi-IN,hi;q=0.9"})
    assert hindi.headers["content-language"] == "hi"
    assert [i["title"] for i in hindi.json()["items"]] == [
        "आपका बॉक्स सोसाइटी पहुँच गया",
        "मंगलवार का बॉक्स पक्का",
        "आपके वॉलेट में ₹2,000 जुड़े",
    ]
    bad = await api.get(f"{INBOX}?cursor=not-a-cursor", headers=auth)
    assert (bad.status_code, bad.json()["code"]) == (422, "request.invalid_cursor")
    assert (await clients().get(INBOX)).status_code == 401


async def test_reading_keeps_the_first_time_is_own_only_and_read_all_clears_the_badge(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, auth, me = await person(clients, google, "+919800011103")
    other_api, other_auth, other = await person(clients, google, "+919800011104")
    mine = await deliver(db_session, me, "orders.dispatched")
    await deliver(db_session, me, "orders.delivered")
    theirs = await deliver(db_session, other, "orders.delivered")
    await db_session.commit()  # a refused request rolls back the shared test session

    assert (await api.post(f"{INBOX}/{mine.id}/read", headers=auth)).status_code == 204
    first_read = (await api.get(INBOX, headers=auth)).json()
    read_at = next(i["read_at"] for i in first_read["items"] if i["id"] == str(mine.id))
    assert read_at is not None and first_read["unread"] == 1
    assert (await api.post(f"{INBOX}/{mine.id}/read", headers=auth)).status_code == 204
    again = (await api.get(INBOX, headers=auth)).json()
    assert next(i["read_at"] for i in again["items"] if i["id"] == str(mine.id)) == read_at

    await db_session.commit()
    for missing in (theirs.id, uuid.uuid4()):
        refused = await api.post(f"{INBOX}/{missing}/read", headers=auth)
        assert (refused.status_code, refused.json()["code"]) == (404, "notifications.not_found")

    assert (await api.post(f"{INBOX}/read-all", headers=auth)).status_code == 204
    assert (await api.get(INBOX, headers=auth)).json()["unread"] == 0
    assert (await other_api.get(INBOX, headers=other_auth)).json()["unread"] == 1


async def test_preferences_default_save_partially_and_refuse_what_the_contract_refuses(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, auth, _ = await person(clients, google, "+919800011105")
    defaults = (await api.get(PREFS, headers=auth)).json()
    assert defaults == {
        "categories": [
            {"category": "weekly_box", "push": True, "whatsapp": False},
            {"category": "delivery", "push": True, "whatsapp": False},
            {"category": "money", "push": True, "whatsapp": False},
            {"category": "kindness", "push": True, "whatsapp": False},
            {"category": "offers", "push": False, "whatsapp": False},
        ],
        "quiet_start": "22:00",
        "quiet_end": "07:00",
    }

    saved = await api.put(
        PREFS,
        json={
            "categories": [
                {"category": "money", "push": False, "whatsapp": False},
                {"category": "offers", "push": True, "whatsapp": False},
            ],
            "quiet_start": "23:00",
        },
        headers=auth,
    )
    assert saved.status_code == 200
    body = saved.json()
    assert {c["category"]: c["push"] for c in body["categories"]} == {
        "weekly_box": True,
        "delivery": True,
        "money": False,
        "kindness": True,
        "offers": True,
    }
    assert (body["quiet_start"], body["quiet_end"]) == ("23:00", "07:00")
    assert (await api.get(PREFS, headers=auth)).json() == body
    await db_session.commit()  # a refused request rolls back the shared test session

    refusals = [
        (
            {"categories": [{"category": "gossip", "push": True, "whatsapp": False}]},
            422,
            "notifications.category_unknown",
        ),
        (
            {"categories": [{"category": "delivery", "push": True, "whatsapp": True}]},
            403,
            "notifications.whatsapp_consent_required",
        ),
        ({"categories": [], "quiet_start": "22:10"}, 422, "notifications.quiet_hours_invalid"),
        ({"categories": [], "quiet_start": "25:00"}, 422, "notifications.quiet_hours_invalid"),
        ({"categories": [], "quiet_end": "23:00"}, 422, "notifications.quiet_hours_invalid"),
    ]
    for payload, status, code in refusals:
        refused = await api.put(PREFS, json=payload, headers=auth)
        assert (refused.status_code, refused.json().get("code")) == (status, code), refused.text
    assert (await api.get(PREFS, headers=auth)).json() == body  # nothing changed

    # With the M1 `whatsapp` consent, WhatsApp may be switched on.
    consent = {"purpose": "whatsapp", "version": "v1", "granted": True}
    assert (await api.post("/v1/consents", json=consent, headers=auth)).status_code == 200
    whatsapp = {"categories": [{"category": "delivery", "push": True, "whatsapp": True}]}
    allowed = await api.put(PREFS, json=whatsapp, headers=auth)
    assert allowed.status_code == 200, allowed.text
    assert {c["category"]: c["whatsapp"] for c in allowed.json()["categories"]}["delivery"]


async def test_the_data_export_section_lists_the_inbox_in_english(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, me = await person(clients, google, "+919800011106")
    message = await deliver(db_session, me, "billing.wallet_topped_up", amount=50_000)

    section = await notifications_api.export_for_user(db_session, me)

    (exported,) = section["messages"]
    assert exported["title"] == "₹500 added to your wallet"
    assert exported["event_type"] == "billing.wallet_topped_up" and exported["read_at"] is None
    assert exported["category"] == "money" and exported["link"] == "/wallet"
    assert exported["deliveries"] == {}  # written by hand here, so never sent
    assert "id" not in exported and message.id is not None
