"""Nutrition per product from IFCT 2017 (M2 §11): on the product, in the API and summed per
recipe serving for the planner."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.models import Product
from gharbasket.tools.seed import CATALOG_SEED, SEED_HUB_ID

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle


def test_every_seed_product_has_a_nutrition_row() -> None:
    products = json.loads((CATALOG_SEED / "products.json").read_text())["products"]
    rows = {
        r["slug"]: r for r in json.loads((CATALOG_SEED / "nutrition.json").read_text())["items"]
    }
    missing = [p["slug"] for p in products if p["slug"] not in rows]
    assert len(missing) <= len(products) * 0.05 and not missing, missing
    for product in products:
        row = rows[product["slug"]]
        assert set(row["per_100g"]) == {"kcal", "protein_g", "fat_g", "carb_g", "fibre_g"}
        if product["step"]["unit"] in ("piece", "bunch"):
            assert row.get("unit_g"), product["slug"]  # grams per piece or bunch
    assert rows["toor-dal"]["per_100g"]["protein_g"] > 20 and rows["ghee"]["per_100g"]["kcal"] > 850
    mapping = Path(__file__).parents[4] / "data/external/nutrition/ifct_mapping.csv"
    assert mapping.exists()


@pytest.mark.anyio
@pytest.mark.services
@pytest.mark.usefixtures("world")
async def test_products_carry_nutrition_and_recipes_sum_it_per_serving(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    products = (await db_session.execute(select(Product).where(Product.active))).scalars().all()
    assert all(p.nutrition and p.nutrition["kcal"] >= 0 for p in products)
    catalog = await catalog_api.plan_catalog(db_session, SEED_HUB_ID)
    dal = next(r for r in catalog.recipes if r.slug == "dal-tadka")
    assert dal.nutrition_per_serving is not None
    assert dal.nutrition_per_serving["kcal"] > 50 and dal.nutrition_per_serving["protein_g"] > 3
    assert (
        sum(r.nutrition_per_serving is not None for r in catalog.recipes)
        >= len(catalog.recipes) * 0.9
    )

    api = clients()
    signed = await sign_in(api, google, phone_number="+919800001001", sub="uid-n")
    tomato = next(p for p in products if p.slug == "tomato")
    body = (
        await api.get(f"/v1/products/{tomato.id}", headers=bearer(signed["access_token"]))
    ).json()
    assert body["nutrition"] == {
        "kcal": 20,
        "protein_g": 0.9,
        "fat_g": 0.5,
        "carb_g": 2.7,
        "fibre_g": 1.8,
        "unit_g": None,
    }
