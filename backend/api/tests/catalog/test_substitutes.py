"""Substitutes (M2 §3.1, US-2): ranked, priced for the hub, and never unsafe for the household."""

import uuid

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"


async def _subs(
    api: httpx2.AsyncClient, headers: dict[str, str], db: AsyncSession, slug: str, **params: str
) -> list[dict]:
    product = (await db.execute(select(Product).where(Product.slug == slug))).scalar_one()
    response = await api.get(
        f"/v1/products/{product.id}/substitutes", params=params, headers=headers
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _tags(db: AsyncSession, slugs: list[str]) -> dict[str, set[str]]:
    rows = await db.execute(select(Product.slug, Product.tags).where(Product.slug.in_(slugs)))
    return {slug: set(tags) for slug, tags in rows.all()}


async def test_three_ranked_substitutes_with_price_difference(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**bearer((await sign_in(api, google, phone_number=PRIYA))["access_token"])}

    items = await _subs(api, headers, db_session, "bhindi")

    assert len(items) == 3
    assert "bhindi" not in [item["slug"] for item in items]
    scores = [item["score"] for item in items]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 1 for s in scores)
    for item in items:
        assert item["price_delta_paise"] == item["price_paise"] - 2000  # okra is ₹20 a step
        if item["reason_code"] == "cheaper":
            assert item["reason_params"] == {"saves_paise": -item["price_delta_paise"]}


@pytest.mark.parametrize(
    ("slug", "never"),
    [
        ("bhindi", {"root", "onion_garlic"}),  # Jain households avoid these
        ("potato", {"onion_garlic"}),  # a root, but not onion or garlic
        ("paneer", {"egg"}),  # vegetarian protein never becomes eggs
    ],
)
async def test_a_substitute_never_adds_what_the_original_avoids(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    slug: str,
    never: set[str],
) -> None:
    api = clients()
    headers = bearer((await sign_in(api, google, phone_number=PRIYA))["access_token"])

    items = await _subs(api, headers, db_session, slug, limit="10")

    tags = await _tags(db_session, [item["slug"] for item in items])
    assert items and all(not (tags[item["slug"]] & never) for item in items)


async def test_without_prices_substitutes_still_come(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = bearer((await sign_in(api, google, phone_number="+919800000098"))["access_token"])

    items = await _subs(api, headers, db_session, "palak", limit="2")

    assert len(items) == 2
    assert all(item["price_paise"] is None and item["price_delta_paise"] is None for item in items)
    assert all(item["reason_code"] in {"same_family", "similar_use"} for item in items)


async def test_an_unknown_product_has_no_substitutes(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = bearer((await sign_in(api, google, phone_number=PRIYA))["access_token"])

    response = await api.get(f"/v1/products/{uuid.uuid4()}/substitutes", headers=headers)

    assert response.status_code == 404
    assert response.json()["code"] == "catalog.product_not_found"
