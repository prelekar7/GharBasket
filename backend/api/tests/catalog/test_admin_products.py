"""The console's product lists (M2 §3.3): every product, active or not, with filters and paging,
one product with its version as the ETag, the categories, and who may read them."""

import uuid

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # customer
VIKRAM = "+919800001008"  # hub manager
NEHA = "+919800001011"  # admin
REKHA = "+919800001013"  # catalog editor
PRODUCTS = "/v1/admin/products"


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed_in = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed_in["access_token"])


async def _id(db: AsyncSession, slug: str) -> uuid.UUID:
    """A product's id (a refused request rolls the session back and expires loaded rows)."""
    return (await db.execute(select(Product.id).where(Product.slug == slug))).scalar_one()


def _code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json()["code"]


async def test_the_admin_list_filters_and_pages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    await db_session.execute(
        Product.__table__.update().where(Product.slug == "mint").values(active=False)  # pyright: ignore[reportAttributeAccessIssue]
    )

    okra = await api.get(PRODUCTS, params={"q": "okra"}, headers=neha)
    fruit = await api.get(PRODUCTS, params={"kind": "fruit", "limit": 3}, headers=neha)
    rest = await api.get(
        PRODUCTS,
        params={"kind": "fruit", "limit": 3, "cursor": fruit.json()["next_cursor"]},
        headers=neha,
    )
    greens = await api.get(PRODUCTS, params={"category": "leafy-greens"}, headers=neha)
    inactive = await api.get(PRODUCTS, params={"active": "false"}, headers=neha)

    assert okra.status_code == 200, okra.text
    assert okra.headers["cache-control"] == "no-store"
    [bhindi] = okra.json()["items"]
    assert bhindi["slug"] == "bhindi" and bhindi["used_by_recipes"] == 3
    assert bhindi["nutrition"]["source"] == "ifct2017"
    assert bhindi["version"] == 1 and bhindi["updated_by"] is None  # never edited in the console
    assert {"term": "bhendi", "lang": "hinglish"} in bhindi["synonyms"]
    first, second = fruit.json()["items"], rest.json()["items"]
    assert len(first) == 3 and {p["kind"] for p in first + second} == {"fruit"}
    assert not {p["id"] for p in first} & {p["id"] for p in second}
    assert "mint" in [p["slug"] for p in greens.json()["items"]]  # inactive ones too
    assert [p["slug"] for p in inactive.json()["items"]] == ["mint"]


async def test_one_product_answers_with_its_version_as_the_etag(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)
    okra = await _id(db_session, "bhindi")
    await db_session.commit()

    one = await api.get(f"{PRODUCTS}/{okra}", headers=neha)
    missing = await api.get(f"{PRODUCTS}/{uuid.uuid4()}", headers=neha)

    assert one.status_code == 200 and one.headers["etag"] == '"1"'
    assert one.json()["names"]["en"] == "Okra (bhindi)" and one.json()["active"] is True
    assert _code(missing) == (404, "catalog.product_not_found")


async def test_who_may_read_the_admin_lists(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, vikram = await _as(api, google, PRIYA), await _as(api, google, VIKRAM)
    rekha = await _as(api, google, REKHA)
    await db_session.commit()

    categories = await api.get("/v1/categories", headers={**priya, "Accept-Language": "mr"})
    assert categories.status_code == 200
    assert categories.json()["items"][0]["slug"] == "leafy-greens"
    assert categories.json()["items"][0]["name"] == "पालेभाज्या"
    assert _code(await api.get(PRODUCTS, headers=priya)) == (403, "auth.forbidden")
    for reader in (vikram, rekha):
        assert (await api.get(PRODUCTS, headers=reader)).status_code == 200
