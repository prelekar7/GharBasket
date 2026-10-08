"""Hub price lists (M2 §3.1, US-3): prices per step in answers, the current list, publishing."""

import uuid
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.catalog.pricing import save_price_list, today
from gharbasket.modules.catalog.schemas import PriceItem
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # customer, household at the seed hub
VIKRAM = "+919800001008"  # hub manager of the seed hub
NEHA = "+919800001011"  # admin


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed_in["access_token"])


async def _ids(db: AsyncSession, *slugs: str) -> list[uuid.UUID]:
    rows = await db.execute(select(Product.slug, Product.id).where(Product.slug.in_(slugs)))
    by_slug = {slug: product_id for slug, product_id in rows.all()}
    return [by_slug[slug] for slug in slugs]


# --- prices in product answers --------------------------------------------------------------


async def test_a_customer_sees_their_hubs_price_per_step(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await _as(api, google, PRIYA)
    [okra] = await _ids(db_session, "bhindi")

    product = (await api.get(f"/v1/products/{okra}", headers=headers)).json()
    found = (await api.get("/v1/search", params={"q": "bhindi"}, headers=headers)).json()
    listed = (await api.get("/v1/products", params={"limit": 100}, headers=headers)).json()

    assert product["price"] == {
        "price_paise": 2000,
        "mrp_paise": None,
        "hub_id": str(SEED_HUB_ID),
        "valid_from": "2026-10-01",
    }
    assert found["items"][0]["price_paise"] == 2000
    assert all(item["price"] is not None for item in listed["items"])


async def test_without_a_household_there_is_no_price_unless_a_hub_is_named(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await _as(api, google, "+919800000099")
    [ghee] = await _ids(db_session, "ghee")

    plain = (await api.get(f"/v1/products/{ghee}", headers=headers)).json()
    named = (
        await api.get(f"/v1/products/{ghee}", params={"hub_id": str(SEED_HUB_ID)}, headers=headers)
    ).json()

    assert plain["price"] is None
    assert (named["price"]["price_paise"], named["price"]["mrp_paise"]) == (35000, 39000)


# --- the current list -----------------------------------------------------------------------


async def test_the_current_list_is_the_newest_on_or_before_the_date(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await _as(api, google, PRIYA)
    [okra] = await _ids(db_session, "bhindi")
    later = today() + timedelta(days=3)
    await save_price_list(
        db_session,
        SEED_HUB_ID,
        later,
        [PriceItem(product_id=okra, price_paise=2400)],
        published_by=None,
    )

    async def on(day: date | None) -> Any:
        params = {"hub_id": str(SEED_HUB_ID), **({"date": day.isoformat()} if day else {})}
        return await api.get("/v1/prices", params=params, headers=headers)

    now, then, before = await on(None), await on(later), await on(date(2026, 9, 30))

    assert now.json()["valid_from"] == "2026-10-01" and len(now.json()["items"]) == 84
    assert then.json()["valid_from"] == later.isoformat()
    assert then.json()["items"] == [
        {"product_id": str(okra), "price_paise": 2400, "mrp_paise": None}
    ]
    assert before.status_code == 404
    assert before.json()["code"] == "catalog.price_list_not_found"
    assert now.headers["cache-control"] == "no-store"


async def test_an_unknown_hub_has_no_prices(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()

    response = await api.get(
        "/v1/prices", params={"hub_id": str(uuid.uuid4())}, headers=await _as(api, google, PRIYA)
    )

    assert response.status_code == 404


# --- publishing -----------------------------------------------------------------------------


def _list(day: date, *items: tuple[uuid.UUID, int, int | None]) -> dict[str, Any]:
    return {
        "valid_from": day.isoformat(),
        "items": [
            {"product_id": str(product), "price_paise": price, "mrp_paise": mrp}
            for product, price, mrp in items
        ],
    }


async def test_a_hub_manager_publishes_once_and_a_retry_replays(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**await _as(api, google, VIKRAM), "Idempotency-Key": "prices-1"}
    okra, ghee = await _ids(db_session, "bhindi", "ghee")
    body = _list(today() + timedelta(days=1), (okra, 2200, None), (ghee, 34000, 39000))
    path = f"/v1/hubs/{SEED_HUB_ID}/price-lists"

    created = await api.post(path, json=body, headers=headers)
    replayed = await api.post(path, json=body, headers=headers)
    again = await api.post(path, json=body, headers={**headers, "Idempotency-Key": "prices-2"})

    assert created.status_code == 201, created.text
    assert created.json() | {"id": "x"} == {
        "id": "x",
        "hub_id": str(SEED_HUB_ID),
        "valid_from": body["valid_from"],
        "items_count": 2,
    }
    assert replayed.json() == created.json()
    assert replayed.headers["idempotent-replayed"] == "true"
    assert again.status_code == 409
    assert again.json()["code"] == "catalog.price_list_exists"
    list_id = created.json()["id"]
    event = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "catalog.prices_published")
        )
    ).scalar_one()
    assert event.payload == {
        "price_list_id": list_id,
        "hub_id": str(SEED_HUB_ID),
        "valid_from": body["valid_from"],
        "items_count": 2,
    }
    audit = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.entity_id == list_id))
    ).scalar_one()
    assert audit.action == "catalog.price_list_published"


@pytest.mark.parametrize(
    ("days", "price", "mrp", "unknown", "code"),
    [
        (-1, 2000, None, False, "catalog.valid_from_past"),
        (1, 3000, 2500, False, "catalog.price_above_mrp"),
        (1, 2000, None, True, "catalog.product_unknown"),
    ],
)
async def test_publishing_rules(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    days: int,
    price: int,
    mrp: int | None,
    unknown: bool,
    code: str,
) -> None:
    api = clients()
    headers = {**await _as(api, google, VIKRAM), "Idempotency-Key": f"rule-{code}"}
    [okra] = await _ids(db_session, "bhindi")
    product = uuid.uuid4() if unknown else okra

    response = await api.post(
        f"/v1/hubs/{SEED_HUB_ID}/price-lists",
        json=_list(today() + timedelta(days=days), (product, price, mrp)),
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["code"] == code
    if code != "catalog.valid_from_past":
        assert response.json()["product_ids"] == [str(product)]


async def test_who_may_publish_where(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    [okra] = await _ids(db_session, "bhindi")
    body = _list(today() + timedelta(days=2), (okra, 2000, None))
    other_hub = uuid.uuid4()

    async def publish(phone: str, hub: uuid.UUID, key: str) -> httpx2.Response:
        headers = {**await _as(api, google, phone), "Idempotency-Key": key}
        return await api.post(f"/v1/hubs/{hub}/price-lists", json=body, headers=headers)

    customer = await publish(PRIYA, SEED_HUB_ID, "c")
    other = await publish(VIKRAM, other_hub, "o")
    admin = await publish(NEHA, other_hub, "a")

    assert customer.status_code == other.status_code == 403
    assert admin.status_code == 201


@pytest.mark.parametrize(
    "change",
    [{"items": []}, {"items": "dup"}, {"items": [{"product_id": "x", "price_paise": 0}]}],
)
async def test_invalid_price_lists_are_422(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, change: dict[str, Any]
) -> None:
    api = clients()
    [okra] = await _ids(db_session, "bhindi")
    body = _list(today() + timedelta(days=1), (okra, 2000, None))
    if change["items"] == "dup":
        body["items"] = body["items"] * 2
    else:
        body.update(change)

    response = await api.post(
        f"/v1/hubs/{SEED_HUB_ID}/price-lists",
        json=body,
        headers={**await _as(api, google, VIKRAM), "Idempotency-Key": "bad"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"
