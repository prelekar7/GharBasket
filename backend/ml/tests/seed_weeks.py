"""Plan requests built from the seed catalog (data/seeds/catalog), as the API will build them."""

import json
from pathlib import Path
from typing import Any

from gharbasket_ml.schemas import PlanRequest

SEED = Path(__file__).resolve().parents[3] / "data" / "seeds" / "catalog"
BASE = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "piece": 1, "bunch": 1}
PERISHABLE_H = 96
# Priya's family (seed): two adults, a teenager and a toddler.
MEMBERS = [
    {"age_band": "18_59", "sex": "f"},
    {"age_band": "18_59", "sex": "m"},
    {"age_band": "5_17", "sex": "f"},
    {"age_band": "under_5", "sex": "m"},
]


def recipes(v1: bool = False) -> list[dict[str, Any]]:
    """The seed recipes as the planner sees them. v0: mains only. v1 (M10 §3.2): sides too
    (flagged `is_side`), each main's sides, and nutrition per serving from IFCT 2017 as the
    catalog computes it (grams x per-100 g values; a piece or bunch weighs its `unit_g`)."""
    products = {p["slug"]: p for p in json.loads((SEED / "products.json").read_text())["products"]}
    prices = {
        i["slug"]: i["price_paise"] for i in json.loads((SEED / "prices.json").read_text())["items"]
    }
    ifct = {i["slug"]: i for i in json.loads((SEED / "nutrition.json").read_text())["items"]}
    out = []
    for r in json.loads((SEED / "recipes.json").read_text())["recipes"]:
        if r.get("is_side") and not v1:
            continue  # a side, not a meal
        tags: set[str] = set()
        cost = 0.0
        perishables = []
        nutrition = {"kcal": 0.0, "protein_g": 0.0, "fibre_g": 0.0}
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
            row = ifct[i["product"]]
            grams = i["qty"] if i["unit"] in ("g", "ml") else i["qty"] * row["unit_g"]
            for key in nutrition:
                nutrition[key] += row["per_100g"][key] * grams / 100
        no_onion_garlic = "onion_garlic" not in tags
        diet = "egg" if "egg" in tags else "veg"
        recipe: dict[str, Any] = {
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
        if v1:
            recipe |= {
                "nutrition_per_serving": {k: round(v, 1) for k, v in nutrition.items()},
                "sides": r.get("sides", []),
                "is_side": bool(r.get("is_side")),
            }
        out.append(recipe)
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


def v1_request(
    fasting_days: list[dict[str, Any]] | None = None, **household_overrides: Any
) -> PlanRequest:
    """Priya's week as a v1 request: members, sides and IFCT nutrition, the default time limit."""
    return PlanRequest.model_validate(
        {
            "household": household(**{"members": MEMBERS, **household_overrides}),
            "fasting_days": fasting_days or [],
            "recipes": recipes(v1=True),
        }
    )
