"""The console's side (spec §3.2): versioned templates with a preview, and the delivery log."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.notifications.models import Delivery, Template
from gharbasket.platform.models import AuditEntry

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .conftest import MEENA, PRIYA, Home
from .test_messages import deliver, messages_of

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TEMPLATES = "/v1/admin/notification-templates"
LOG = "/v1/admin/notifications"
NEHA = "+919800001011"  # admin
VIKRAM = "+919800001008"  # hub manager of the seed hub


async def staff(
    clients: ClientFactory, google: FakeGoogle, phone: str
) -> tuple[httpx2.AsyncClient, dict[str, str]]:
    api = clients()
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return api, bearer(signed["access_token"])


def locked(**texts: tuple[str, str]) -> dict[str, Any]:
    """An app template for orders.locked; each locale's (title, body) can be swapped."""
    copy = {
        "en-IN": ("Box for {day} confirmed", "{total} paid."),
        "hi-IN": ("{day} का बॉक्स पक्का", "{total} चुकाए।"),
        "mr-IN": ("{day} चा बॉक्स निश्चित", "{total} भरले."),
        **{locale.replace("_", "-"): text for locale, text in texts.items()},
    }
    return {
        "kind": "orders.locked",
        "channel": "app",
        "texts": [{"locale": loc, "title": t, "body": b} for loc, (t, b) in copy.items()],
    }


async def test_saving_makes_a_new_active_version_in_every_locale_and_keeps_the_old_one(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    api, auth = await staff(clients, google, NEHA)

    saved = await api.put(TEMPLATES, json=locked(), headers=auth)

    assert saved.status_code == 200, saved.text
    items = saved.json()["items"]
    assert [(t["locale"], t["version"], t["active"]) for t in items] == [
        ("en-IN", 2, True),
        ("hi-IN", 2, True),
        ("mr-IN", 2, True),
    ]
    assert items[0]["title"] == "Box for {day} confirmed"
    active = (await api.get(TEMPLATES, params={"kind": "orders.locked"}, headers=auth)).json()
    assert [t["version"] for t in active["items"]] == [2, 2, 2]
    kind = {"kind": "orders.locked", "event_type": "orders.locked", "category": "money"}
    assert {**kind, "placeholders": ["total", "day"]} in active["kinds"]
    history = await api.get(
        TEMPLATES,
        params={"kind": "orders.locked", "locale": "en-IN", "channel": "app", "history": "true"},
        headers=auth,
    )
    assert [(t["version"], t["active"]) for t in history.json()["items"]] == [(2, True), (1, False)]

    # Messages read the new copy.
    order = {"delivery_date": "2026-10-13", "total_paise": 69_000}
    await deliver(
        db_session, "orders.locked", {"household_id": str(homes[PRIYA].household_id), **order}
    )
    log = await api.get(LOG, params={"user_id": str(homes[PRIYA].user_id)}, headers=auth)
    assert log.json()["items"][0]["title"] == "Box for Tuesday confirmed"

    audit = await db_session.scalar(
        select(AuditEntry).where(AuditEntry.action == "notifications.template_saved")
    )
    assert audit is not None
    assert audit.entity_id == "orders.locked/app"
    assert audit.before is not None and audit.before["en-IN"]["version"] == 1
    assert audit.after is not None and audit.after["hi-IN"]["title"] == "{day} का बॉक्स पक्का"


async def test_a_whatsapp_template_names_metas_template_and_previews_its_parameters(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    api, auth = await staff(clients, google, NEHA)
    body = {
        **locked(),
        "channel": "whatsapp",
        "wa_template_name": "box_confirmed",
        "texts": [
            {"locale": "en-IN", "title": "", "body": "Your box for {day} is confirmed: {total}."},
            {"locale": "hi-IN", "title": "", "body": "{total}: {day} का बॉक्स पक्का।"},
            {"locale": "mr-IN", "title": "", "body": "{day} चा बॉक्स निश्चित: {total}."},
        ],
    }

    preview = await api.post(f"{TEMPLATES}/preview", json=body, headers=auth)

    assert preview.status_code == 200, preview.text
    assert [(p["locale"], p["title"], p["body"], p["params"]) for p in preview.json()["items"]] == [
        ("en-IN", "", "Your box for Tuesday is confirmed: ₹690.", ["Tuesday", "₹690"]),
        ("hi-IN", "", "₹690: मंगलवार का बॉक्स पक्का।", ["₹690", "मंगलवार"]),
        ("mr-IN", "", "मंगळवार चा बॉक्स निश्चित: ₹690.", ["मंगळवार", "₹690"]),
    ]
    assert await db_session.scalar(select(Template).where(Template.channel == "whatsapp")) is None

    saved = await api.put(TEMPLATES, json=body, headers=auth)
    assert [(t["version"], t["wa_template_name"]) for t in saved.json()["items"]] == [
        (1, "box_confirmed")
    ] * 3


@pytest.mark.parametrize(
    ("change", "where"),
    [
        ({"kind": "orders.unknown"}, ["body", "kind"]),
        ({"channel": "sms"}, ["body", "channel"]),
        ({"texts": locked()["texts"][:2]}, ["body", "texts"]),
        ({"wa_template_name": "box_confirmed"}, ["body", "wa_template_name"]),
        (
            {"channel": "whatsapp", "wa_template_name": "Box Confirmed"},
            ["body", "wa_template_name"],
        ),
        (locked(hi_IN=("{day}", "{amount} चुकाए।")), ["body", "texts", 1, "body"]),
        (locked(en_IN=("{day:>10}", "{total} paid.")), ["body", "texts", 0, "title"]),
        (locked(mr_IN=("{day", "{total}")), ["body", "texts", 2, "title"]),
        (locked(en_IN=(" ", "{total} paid.")), ["body", "texts", 0, "title"]),
    ],
)
async def test_an_invalid_template_is_refused_with_every_reason(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    homes: dict[str, Home],
    change: dict[str, Any],
    where: list[Any],
) -> None:
    api, auth = await staff(clients, google, NEHA)
    await db_session.commit()

    for answer in (
        await api.put(TEMPLATES, json={**locked(), **change}, headers=auth),
        await api.post(f"{TEMPLATES}/preview", json={**locked(), **change}, headers=auth),
    ):
        assert answer.status_code == 422, answer.text
        problem = answer.json()
        assert problem["code"] == "notifications.template_invalid"
        assert where in [e["loc"] for e in problem["errors"]]
    versions = await db_session.scalars(
        select(Template.version).where(Template.kind == "orders.locked", Template.active)
    )
    assert set(versions) == {1}


async def test_only_admins_edit_templates_and_hub_managers_see_only_their_hubs_people(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    vikram_api, vikram = await staff(clients, google, VIKRAM)
    priya_api, priya = await staff(clients, google, PRIYA)
    _, stranger = await staff(clients, google, "+919800012001")  # no household anywhere
    stranger_id = (await priya_api.get("/v1/me", headers=stranger)).json()["id"]
    await db_session.commit()

    assert (await vikram_api.get(TEMPLATES, headers=vikram)).status_code == 403
    assert (await priya_api.put(TEMPLATES, json=locked(), headers=priya)).status_code == 403
    mine = {"user_id": str(homes[PRIYA].user_id)}
    assert (await priya_api.get(LOG, params=mine, headers=priya)).status_code == 403
    assert (await vikram_api.get(LOG, params=mine, headers=vikram)).status_code == 200
    other = await vikram_api.get(LOG, params={"user_id": stranger_id}, headers=vikram)
    assert (other.status_code, other.json()["code"]) == (403, "auth.forbidden")


async def test_the_delivery_log_shows_each_message_with_its_attempts_newest_first(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    api, auth = await staff(clients, google, NEHA)
    meena = homes[MEENA]
    household = {"household_id": str(meena.household_id)}
    first = await deliver(db_session, "orders.dispatched", household)
    second = await deliver(db_session, "orders.delivered", household)
    (message,) = await messages_of(db_session, second)
    push = await db_session.scalar(select(Delivery).where(Delivery.message_id == message.id))
    assert push is not None
    push.status, push.attempts, push.last_error = "failed", 4, "HTTP 503 UNAVAILABLE"
    await db_session.flush()

    page = await api.get(
        LOG,
        params={"user_id": str(meena.user_id), "limit": 1},
        headers={**auth, "Accept-Language": "hi"},
    )

    assert page.status_code == 200, page.text
    assert page.headers["cache-control"] == "no-store"
    (entry,) = page.json()["items"]
    assert (entry["event_id"], entry["kind"], entry["link"]) == (
        str(second),
        "orders.delivered",
        "/orders",
    )
    assert entry["title"]  # rendered, in Hindi
    assert entry["deliveries"] == [
        {
            "channel": "push",
            "status": "failed",
            "attempts": 4,
            "last_error": "HTTP 503 UNAVAILABLE",
            "provider_ref": None,
            "next_attempt_at": entry["deliveries"][0]["next_attempt_at"],
            "sent_at": None,
        }
    ]
    rest = await api.get(
        LOG,
        params={"user_id": str(meena.user_id), "cursor": page.json()["next_cursor"]},
        headers=auth,
    )
    assert [e["event_id"] for e in rest.json()["items"]] == [str(first)]
    assert rest.json()["next_cursor"] is None
    nobody = await api.get(LOG, params={"user_id": str(uuid.uuid4())}, headers=auth)
    assert nobody.json() == {"items": [], "next_cursor": None}
