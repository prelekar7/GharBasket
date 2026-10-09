"""Products (M2 §3.1): listing, filters, one product, languages, caching and data quality."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Category, Product

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("catalog")]


async def _customer(api: httpx2.AsyncClient, google: FakeGoogle) -> dict[str, str]:
    return bearer((await sign_in(api, google))["access_token"])


async def _all_pages(
    api: httpx2.AsyncClient, headers: dict[str, str], **params: Any
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        page = await api.get("/v1/products", params=query, headers=headers)
        assert page.status_code == 200, page.text
        items += page.json()["items"]
        cursor = page.json()["next_cursor"]
        if cursor is None:
            return items
    raise AssertionError("pagination did not end")


async def test_products_are_alphabetical_and_pages_join_up(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await _customer(api, google)

    everything = await _all_pages(api, headers, limit=100)
    paged = await _all_pages(api, headers, limit=7)

    assert len(everything) == 91
    assert paged == everything
    english = [item["names"]["en"] for item in everything]
    assert english == sorted(english)


async def test_a_product_in_english_hindi_and_marathi(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await _customer(api, google)
    okra = (await db_session.execute(select(Product).where(Product.slug == "bhindi"))).scalar_one()

    answers = {
        lang: await api.get(
            f"/v1/products/{okra.id}", headers={**headers, "Accept-Language": header}
        )
        for lang, header in [("en", "en-IN"), ("hi", "hi-IN,en;q=0.5"), ("mr", "mr")]
    }

    english = answers["en"].json()
    assert english == {
        "id": str(okra.id),
        "slug": "bhindi",
        "kind": "veg",
        "category": {
            "id": english["category"]["id"],
            "slug": "vegetables",
            "name": "Vegetables",
        },
        "name": "Okra (bhindi)",
        "names": {"en": "Okra (bhindi)", "hi": "भिंडी", "mr": "भेंडी"},
        "step": {"qty": 250, "unit": "g", "label": "250 g"},
        "tags": ["high_fibre"],
        "shelf_life_h": 72,
        "image_url": None,
        "price": None,
        "nutrition": english["nutrition"],  # IFCT 2017 ladies finger: tests/catalog/test_nutrition
    }
    assert english["nutrition"]["fibre_g"] == 4.1 and english["nutrition"]["kcal"] > 0
    assert answers["hi"].json()["name"] == "भिंडी"
    assert answers["hi"].json()["category"]["name"] == "सब्ज़ियाँ"
    assert answers["mr"].json()["step"]["label"] == "250 ग्रॅम"
    for lang, response in answers.items():
        assert response.headers["content-language"] == lang
        assert response.headers["vary"] == "Accept-Language"
        assert response.headers["cache-control"] == "private, max-age=300"


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"kind": "fruit"}, 12),
        ({"category": "leafy-greens"}, 8),
        ({"tag": "onion_garlic"}, 3),
        ({"kind": "veg", "tag": "root"}, 8),
        ({"category": "no-such-category"}, 0),
    ],
)
async def test_filters(
    clients: ClientFactory, google: FakeGoogle, params: dict[str, str], expected: int
) -> None:
    api = clients()
    headers = await _customer(api, google)

    items = await _all_pages(api, headers, **params)

    assert len(items) == expected


async def test_unknown_or_inactive_products_are_404(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await _customer(api, google)
    okra = (await db_session.execute(select(Product).where(Product.slug == "bhindi"))).scalar_one()
    okra_id = okra.id
    okra.active = False
    # Commit (a savepoint here), so a failing request's rollback cannot undo it.
    await db_session.commit()

    listed = [item["slug"] for item in await _all_pages(api, headers, limit=100)]
    unknown = await api.get(f"/v1/products/{uuid.uuid4()}", headers=headers)
    inactive = await api.get(f"/v1/products/{okra_id}", headers=headers)

    for response in (unknown, inactive):
        assert response.status_code == 404
        assert response.json()["code"] == "catalog.product_not_found"
    assert "bhindi" not in listed


async def test_a_tampered_cursor_is_refused(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    headers = await _customer(api, google)

    response = await api.get(
        "/v1/products", params={"cursor": "eyJhZnRlciI6WzFdfQ"}, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid_cursor"


async def test_products_need_sign_in(clients: ClientFactory) -> None:
    assert (await clients().get("/v1/products")).status_code == 401


async def test_a_product_cannot_miss_a_language(db_session: AsyncSession) -> None:
    category = (await db_session.execute(select(Category))).scalars().first()
    assert category is not None
    db_session.add(
        Product(
            slug="nameless",
            kind="veg",
            category_id=category.id,
            names={"en": "Nameless", "hi": "बेनाम"},
            step_qty=1,
            step_unit="piece",
        )
    )

    with pytest.raises(IntegrityError, match="ck_products_names"):
        await db_session.flush()
