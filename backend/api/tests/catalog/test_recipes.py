"""Recipes and festivals (M2 §3.2): derived diet rules, filters, ingredients, cost, calendar."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Recipe
from gharbasket.modules.catalog.recipes import (
    IngredientData,
    RecipeData,
    save_recipe,
)

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # household at the seed hub, so prices apply


async def _headers(
    api: httpx2.AsyncClient, google: FakeGoogle, phone: str = PRIYA
) -> dict[str, str]:
    return bearer((await sign_in(api, google, phone_number=phone, sub=f"u{phone}"))["access_token"])


async def _recipes(api: httpx2.AsyncClient, headers: dict[str, str], **params: Any) -> list[Any]:
    items: list[Any] = []
    cursor = None
    for _ in range(20):
        query = {**params, **({"cursor": cursor} if cursor else {})}
        page = await api.get("/v1/recipes", params=query, headers=headers)
        assert page.status_code == 200, page.text
        items += page.json()["items"]
        if (cursor := page.json()["next_cursor"]) is None:
            return items
    raise AssertionError("pagination did not end")


async def _id(db: AsyncSession, slug: str) -> uuid.UUID:
    return (await db.execute(select(Recipe.id).where(Recipe.slug == slug))).scalar_one()


async def test_recipes_alphabetical_with_pages_and_filters(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await _headers(api, google)

    everything = await _recipes(api, headers, limit=100)
    paged = await _recipes(api, headers, limit=5)
    jain = await _recipes(api, headers, jain_ok="true")
    fasting = await _recipes(api, headers, fasting_ok="true")
    breakfast = await _recipes(api, headers, slot="breakfast")
    gujarati = await _recipes(api, headers, cuisine="gujarati")
    egg = await _recipes(api, headers, diet="egg")

    assert len(everything) == 22 and paged == everything
    english = [r["names"]["en"] for r in everything]
    assert english == sorted(english)
    assert jain and all(r["rules"]["jain_ok"] for r in jain)
    assert {r["slug"] for r in fasting} == {"sabudana-khichdi", "kakdi-koshimbir", "fruit-bowl"}
    assert all("breakfast" in r["slots"] for r in breakfast)
    assert [r["slug"] for r in gujarati] == ["methi-thepla"]
    assert [r["slug"] for r in egg] == ["egg-curry"]


async def test_a_recipe_with_ingredients_labels_and_cost(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**await _headers(api, google), "Accept-Language": "mr"}
    recipe_id = await _id(db_session, "varan-bhaat")

    recipe = (await api.get(f"/v1/recipes/{recipe_id}", headers=headers)).json()

    assert recipe["name"] == "वरण भात"
    assert recipe["diet"] == "veg"
    assert recipe["rules"] == {
        "jain_ok": True,
        "no_onion_garlic": True,
        "fasting_ok": False,
        "diabetic_ok": False,
    }
    by_slug = {i["slug"]: i for i in recipe["ingredients"]}
    assert by_slug["toor-dal"] | {"product_id": "x"} == {
        "product_id": "x",
        "slug": "toor-dal",
        "name": "तूर डाळ",
        "qty": 40.0,
        "unit": "g",
        "label": "40 ग्रॅम",
        "optional": False,
    }
    # Per serving at the seed hub: toor 40/500 x ₹90 + rice 75/1000 x ₹70 + turmeric 1/100 x ₹30
    # + salt 3/1000 x ₹25 + ghee 5/500 x ₹350 = ₹16.325 → 1632 paise (half to even).
    assert recipe["cost_per_serving_paise"] == 1632


async def test_optional_ingredients_come_last_and_cost_needs_a_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    recipe_id = await _id(db_session, "kanda-poha")

    priya = (await api.get(f"/v1/recipes/{recipe_id}", headers=await _headers(api, google))).json()
    nobody = (
        await api.get(
            f"/v1/recipes/{recipe_id}", headers=await _headers(api, google, "+919800000097")
        )
    ).json()

    flags = [i["optional"] for i in priya["ingredients"]]
    assert flags == sorted(flags) and flags[-1] is True
    assert [i["label"] for i in priya["ingredients"] if i["slug"] == "lemon"] == ["0.25 pc"]
    assert isinstance(priya["cost_per_serving_paise"], int)
    assert nobody["cost_per_serving_paise"] is None


async def test_an_unknown_recipe_is_404(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()

    response = await api.get(f"/v1/recipes/{uuid.uuid4()}", headers=await _headers(api, google))

    assert response.status_code == 404
    assert response.json()["code"] == "catalog.recipe_not_found"


async def test_a_recipe_must_use_known_products_in_their_units(db_session: AsyncSession) -> None:
    def recipe(product: str, unit: str) -> RecipeData:
        return RecipeData(
            slug="test-dish",
            names={"en": "Test", "hi": "टेस्ट", "mr": "टेस्ट"},
            cuisine="common",
            slots=("lunch",),
            cook_minutes=10,
            servings=2,
            fasting_ok=False,
            diabetic_ok=False,
            ingredients=(IngredientData(product, Decimal(10), unit),),
        )

    with pytest.raises(ValueError, match="unknown products"):
        await save_recipe(db_session, recipe("unicorn", "g"))
    with pytest.raises(ValueError, match="measured in g"):
        await save_recipe(db_session, recipe("onion", "piece"))


# --- festivals --------------------------------------------------------------------------------


async def test_festivals_in_a_range_with_names_and_the_unverified_flag(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = {**await _headers(api, google), "Accept-Language": "hi"}

    response = await api.get(
        "/v1/festivals",
        params={"from": "2026-10-01", "to": "2026-10-31", "region": "maharashtra"},
        headers=headers,
    )
    none = await api.get(
        "/v1/festivals", params={"from": "2026-11-01", "to": "2026-11-30"}, headers=headers
    )

    items = response.json()["items"]
    assert [i["date"] for i in items] == [date(2026, 10, d).isoformat() for d in range(11, 20)]
    assert items[0] == {
        "date": "2026-10-11",
        "code": "navratri",
        "name": "नवरात्रि",
        "region": "all",
        "rules": {"fasting": True, "no_onion_garlic": True},
        "verified": False,
    }
    assert none.json()["items"] == []


@pytest.mark.parametrize(
    "params",
    [{"from": "2026-10-01", "to": "2027-01-15"}, {"from": "2026-10-10", "to": "2026-10-01"}],
)
async def test_a_festival_range_is_0_to_92_days(
    clients: ClientFactory, google: FakeGoogle, params: dict[str, str]
) -> None:
    api = clients()

    response = await api.get("/v1/festivals", params=params, headers=await _headers(api, google))

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"
