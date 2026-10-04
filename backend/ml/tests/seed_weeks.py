"""Plan requests built from the seed catalog (data/seeds/catalog), as the API will build them."""

import json
from pathlib import Path
from typing import Any

from gharbasket_ml.schemas import PlanRequest

SEED = Path(__file__).resolve().parents[3] / "data" / "seeds" / "catalog"
BASE = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "piece": 1, "bunch": 1}
PERISHABLE_H = 96


def recipes() -> list[dict[str, Any]]:
    products = {p["slug"]: p for p in json.loads((SEED / "products.json").read_text())["products"]}
    prices = {
        i["slug"]: i["price_paise"] for i in json.loads((SEED / "prices.json").read_text())["items"]
    }
    out = []
    for r in json.loads((SEED / "recipes.json").read_text())["recipes"]:
        if r["tags"] == ["roti"]:
            continue  # a side, not a meal
        tags: set[str] = set()
        cost = 0.0
        perishables = []
        for i in r["ingredients"]:
            product = products[i["product"]]
            tags |= set(product["tags"])
            cost += (
                i["qty"]
                / (product["step"]["qty"] * BASE[product["step"]["unit"]])
                * prices[i["product"]]
            )
            if product["shelf_life_h"] <= PERISHABLE_H:
                perishables.append(i["product"])
        no_onion_garlic = "onion_garlic" not in tags
        diet = "egg" if "egg" in tags else "veg"
        out.append(
            {
                "slug": r["slug"],
                "cuisine": r["cuisine"],
                "slots": r["slots"],
                "cook_minutes": r["cook_minutes"],
                "diet": diet,
                "jain_ok": no_onion_garlic and "root" not in tags and diet == "veg",
                "no_onion_garlic": no_onion_garlic,
                "fasting_ok": r["fasting_ok"],
                "diabetic_ok": r["diabetic_ok"],
                "no_cook": "no_cook" in r["tags"],
                "cost_per_serving_paise": round(cost),
                "perishables": perishables,
            }
        )
    return out


def household(**overrides: Any) -> dict[str, Any]:
    """Priya's household (seed): four, veg, Sunday no-cook, ₹6,000 a month."""
    return {
        "size": 4,
        "diet": "veg",
        "cuisines": ["maharashtrian", "north_indian"],
        "weekday_cook_minutes": 30,
        "weekend_cook_minutes": 60,
        "no_cook_days": [6],
        "weekly_budget_paise": 138568,
        **overrides,
    }


def request(
    fasting_days: list[dict[str, Any]] | None = None, **household_overrides: Any
) -> PlanRequest:
    return PlanRequest.model_validate(
        {
            "household": household(**household_overrides),
            "fasting_days": fasting_days or [],
            "recipes": recipes(),
            "time_limit_s": 2.0,
        }
    )
