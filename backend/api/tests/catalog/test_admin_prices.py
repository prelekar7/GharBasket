"""The console's price lists (M2 §3.3): the list and one list with its items, replacing today's
or a future list with If-Match, deleting a future one, past lists locked, events, audit and who
may touch which hub."""

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

PRIYA = "+919800001001"  # customer
VIKRAM = "+919800001008"  # hub manager of the seed hub
NEHA = "+919800001011"  # admin
LISTS = f"/v1/hubs/{SEED_HUB_ID}/price-lists"


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed_in["access_token"])


async def _product(db: AsyncSession, slug: str) -> uuid.UUID:
    return (await db.execute(select(Product.id).where(Product.slug == slug))).scalar_one()


async def _list(
    db: AsyncSession, day: date, hub: uuid.UUID = SEED_HUB_ID, price: int = 2000
) -> str:
    okra, ghee = await _product(db, "bhindi"), await _product(db, "ghee")
    items = [
        PriceItem(product_id=okra, price_paise=price, mrp_paise=None),
        PriceItem(product_id=ghee, price_paise=34000, mrp_paise=39000),
    ]
    return str((await save_price_list(db, hub, day, items, published_by=None)).id)


def _items(*lines: tuple[uuid.UUID, int, int | None]) -> dict[str, Any]:
    return {
        "items": [
            {"product_id": str(p), "price_paise": price, "mrp_paise": mrp}
            for p, price, mrp in lines
        ]
    }


async def _put(
    api: httpx2.AsyncClient, auth: dict[str, str], path: str, body: dict, version: Any = 1
) -> httpx2.Response:
    headers = {**auth, "If-Match": f'"{version}"'} if version is not None else auth
    return await api.put(path, json=body, headers=headers)


def _code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["code"]


async def test_the_list_and_one_list_with_its_items(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    old = await _list(db_session, today() - timedelta(days=60))
    past = await _list(db_session, today() - timedelta(days=3))
    future = await _list(db_session, today() + timedelta(days=90))
    await db_session.commit()

    lists = await api.get(LISTS, headers=vikram)
    every = await api.get(LISTS, params={"from": "2000-01-01"}, headers=vikram)
    one = await api.get(f"{LISTS}/{future}", headers={**vikram, "Accept-Language": "hi"})

    assert lists.status_code == 200, lists.text
    found = lists.json()["items"]
    assert found[0]["id"] == future and past in [p["id"] for p in found]
    assert old not in [p["id"] for p in found]  # older than 30 days
    assert old in [p["id"] for p in every.json()["items"]]
    by_id = {p["id"]: p for p in found}
    assert by_id[future]["editable"] is True and by_id[past]["editable"] is False
    assert by_id[future]["items_count"] == 2 and by_id[future]["version"] == 1
    assert one.status_code == 200 and one.headers["etag"] == '"1"'
    okra = next(i for i in one.json()["items"] if i["slug"] == "bhindi")
    assert okra["name"] == "भिंडी" and okra["step"]["qty"] == 250 and okra["price_paise"] == 2000
    assert _code(await api.get(f"{LISTS}/{uuid.uuid4()}", headers=vikram)) == (
        404,
        "catalog.price_list_not_found",
    )


async def test_a_future_list_is_replaced_with_if_match(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    future = await _list(db_session, today() + timedelta(days=7))
    okra, palak = await _product(db_session, "bhindi"), await _product(db_session, "palak")
    path = f"{LISTS}/{future}"
    body = _items((okra, 2400, None), (palak, 2600, 3000))
    await db_session.commit()

    assert _code(await _put(api, vikram, path, body, version=None)) == (
        428,
        "request.precondition_required",
    )
    assert _code(await _put(api, vikram, path, body, version=3)) == (
        412,
        "catalog.version_conflict",
    )
    above = await _put(api, vikram, path, _items((okra, 3000, 2500)))
    assert _code(above) == (422, "catalog.price_above_mrp")
    ghost = uuid.uuid4()
    unknown = await _put(api, vikram, path, _items((ghost, 3000, None)))
    assert _code(unknown) == (422, "catalog.product_unknown")
    assert unknown.json()["product_ids"] == [str(ghost)]
    assert _code(await _put(api, vikram, path, {"items": []})) == (422, "request.invalid")

    replaced = await _put(api, vikram, path, body)
    again = await _put(api, vikram, path, body, version=2)

    assert replaced.status_code == 200, replaced.text
    out = replaced.json()
    assert out["version"] == 2 and replaced.headers["etag"] == '"2"'
    assert sorted(i["slug"] for i in out["items"]) == ["bhindi", "palak"]
    assert again.json()["version"] == 2  # nothing changed, nothing saved
    [event] = (
        await db_session.execute(
            select(OutboxEvent.payload).where(OutboxEvent.event_type == "catalog.prices_published")
        )
    ).scalars()
    assert event == {
        "price_list_id": future,
        "hub_id": str(SEED_HUB_ID),
        "valid_from": (today() + timedelta(days=7)).isoformat(),
        "items_count": 2,
        "replaced": True,
    }
    [entry] = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.entity_id == future))
    ).scalars()
    assert entry.action == "catalog.price_list_updated"
    assert entry.before is not None and entry.after is not None
    assert len(entry.before["items"]) == 2
    assert {i["price_paise"] for i in entry.after["items"]} == {2400, 2600}


async def test_todays_list_changes_only_later_reads_and_cannot_be_deleted(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    current = await _list(db_session, today())
    okra = await _product(db_session, "bhindi")
    await db_session.commit()

    replaced = await _put(api, vikram, f"{LISTS}/{current}", _items((okra, 2100, None)))
    deleted = await api.delete(f"{LISTS}/{current}", headers=vikram)

    assert replaced.status_code == 200, replaced.text
    prices = await api.get("/v1/prices", params={"hub_id": str(SEED_HUB_ID)}, headers=vikram)
    assert prices.json()["items"] == [
        {"product_id": str(okra), "price_paise": 2100, "mrp_paise": None}
    ]
    assert _code(deleted) == (409, "catalog.price_list_locked")


async def test_a_past_list_is_locked_and_a_future_one_can_be_deleted(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    past = await _list(db_session, today() - timedelta(days=1))
    future = await _list(db_session, today() + timedelta(days=2))
    okra = await _product(db_session, "bhindi")
    await db_session.commit()

    put = await _put(api, neha, f"{LISTS}/{past}", _items((okra, 1, None)))
    gone = await api.delete(f"{LISTS}/{past}", headers=neha)
    deleted = await api.delete(f"{LISTS}/{future}", headers=neha)

    assert _code(put) == (409, "catalog.price_list_locked")
    assert _code(gone) == (409, "catalog.price_list_locked")
    assert deleted.status_code == 204
    assert _code(await api.get(f"{LISTS}/{future}", headers=neha)) == (
        404,
        "catalog.price_list_not_found",
    )
    [entry] = (
        await db_session.execute(select(AuditEntry).where(AuditEntry.entity_id == future))
    ).scalars()
    assert entry.action == "catalog.price_list_deleted" and entry.before is not None
    assert entry.before["valid_from"] == (today() + timedelta(days=2)).isoformat()


async def test_a_hub_manager_touches_only_their_hubs_lists(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    priya = await _as(api, google, PRIYA)
    other_hub = uuid.uuid4()
    theirs = await _list(db_session, today() + timedelta(days=5), hub=other_hub)
    ours = await _list(db_session, today() + timedelta(days=5))
    okra = await _product(db_session, "bhindi")
    await db_session.commit()
    elsewhere = f"/v1/hubs/{other_hub}/price-lists"

    refused = [
        await api.get(elsewhere, headers=vikram),
        await api.get(f"{elsewhere}/{theirs}", headers=vikram),
        await _put(api, vikram, f"{elsewhere}/{theirs}", _items((okra, 1, None))),
        await api.delete(f"{elsewhere}/{theirs}", headers=vikram),
        await api.get(LISTS, headers=priya),
    ]

    assert [_code(answer) for answer in refused] == [(403, "auth.forbidden")] * 5
    assert (await api.get(f"{elsewhere}/{theirs}", headers=neha)).status_code == 200
    # A list is read through its own hub only.
    assert _code(await api.get(f"{LISTS}/{theirs}", headers=vikram)) == (
        404,
        "catalog.price_list_not_found",
    )
    assert (await api.get(f"{LISTS}/{ours}", headers=vikram)).status_code == 200
