"""Households sampled from the cohorts, with the latent truth the models must recover.

Pure and deterministic: the same seed, cohorts, societies and catalog give the same households.
Each household has
- an onboarding request, exactly what the app sends to `POST /v1/households`;
- the planner's rules for it (M10 §3.1);
- latent truth: weekly daily-use rates per product (milk, atta, fruit…), a taste for every
  recipe, how readily it accepts a plan, and how often it skips a week.
"""

import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gharbasket.tools.seed import SOCIETIES, SocietyData
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.config import Cohort, Config

# +91 98001 00001 onwards are reserved for simulated people (`just seed` uses 98000 01xxx).
PHONE_PREFIX = "+9198001"
CONSENT_VERSION = "v1"


@dataclass(frozen=True)
class SimHousehold:
    key: str  # "h0001": stable across runs with the same seed
    phone: str
    society_id: str
    cohort: str
    locale: str
    body: dict[str, Any]  # POST /v1/households
    rules: dict[str, Any]  # the planner's HouseholdRules
    fasts: tuple[str, ...]
    no_onion_garlic_on_fasts: bool
    adult_equivalents: float
    daily_use: dict[str, float]  # product → base units a week (ground truth)
    taste: dict[str, float]  # recipe → liking, as a logit (ground truth)
    accept_bias: float
    skip_week: float


def societies() -> list[SocietyData]:
    """The seed societies that have a hub and towers: the simulator lives in the seed world."""
    return [s for s in SOCIETIES if s.hub_id is not None and s.towers and s.flats]


def _pick[K: str](rng: random.Random, weights: Mapping[K, float]) -> K:
    keys = sorted(weights)
    return rng.choices(keys, [weights[k] for k in keys])[0]


def _members(rng: random.Random, c: Cohort) -> list[dict[str, Any]]:
    adults = rng.randint(*c.adults)
    seniors = rng.randint(*c.seniors)
    children = rng.randint(*c.children)
    members: list[dict[str, Any]] = []
    for i in range(adults):
        members.append({"age_band": "18_59", "sex": ("f", "m")[i % 2]})
    for i in range(seniors):
        members.append({"age_band": "60_plus", "sex": ("m", "f")[i % 2]})
    for _ in range(children):
        members.append({"age_band": rng.choice(["under_5", "5_17"]), "sex": rng.choice("fm")})
    return members


def sample(
    seed: int, config: Config, catalog: Catalog, adoption: float | None = None
) -> list[SimHousehold]:
    rng = random.Random(f"gharbasket-sim:{seed}")
    cohorts = {c.name: c for c in config.cohorts}
    weights = {c.name: c.weight for c in config.cohorts}
    share = config.adoption if adoption is None else adoption
    out: list[SimHousehold] = []
    for society in societies():
        flats = society.flats or 0
        towers = [t["id"] for t in society.towers]
        for k in range(round(flats * share)):
            c = cohorts[_pick(rng, weights)]
            n = len(out) + 1
            tower = towers[k % len(towers)]
            j = k // len(towers)  # this tower's j-th household: four flats a floor
            flat = f"{tower.upper()}-{1 + j // 4}{1 + j % 4:02d}"
            out.append(_household(rng, config, catalog, c, n, str(society.id), tower, flat))
    return out


def _household(
    rng: random.Random,
    config: Config,
    catalog: Catalog,
    c: Cohort,
    n: int,
    society_id: str,
    tower: str,
    flat: str,
) -> SimHousehold:
    members = _members(rng, c)
    jain = rng.random() < c.jain
    diet = "veg" if jain else _pick(rng, c.diet)  # a Jain member needs a veg household
    cuisines = [c.cuisines[0]]
    if len(c.cuisines) > 1 and rng.random() < c.second_cuisine:
        cuisines.append(c.cuisines[1])
    fasts = tuple(sorted(f for f, p in c.fasts.items() if rng.random() < p))
    no_onion_garlic = rng.random() < c.no_onion_garlic_on_fasts
    diabetic = rng.random() < c.diabetic
    weekday = rng.choice(c.weekday_cook_minutes)
    weekend = rng.choice(c.weekend_cook_minutes)
    no_cook_days = [6] if rng.random() < c.no_cook_sunday else []
    budget = None
    if rng.random() < c.budget_on:
        budget = round(rng.randint(*c.budget_paise) / 50_000) * 50_000

    elder = next((m for m in members if m["age_band"] == "60_plus"), members[0])
    for m in members:
        m["fasting_rules"] = {"jain": jain}
    elder["fasting_rules"] |= {
        "fasts": list(fasts),
        "no_onion_garlic_on_fast_days": no_onion_garlic,
    }
    if diabetic:
        elder["health_tags"] = ["diabetes"]
    purposes = ["service_delivery", "personalisation"]
    if diabetic:
        purposes.append("health_personalisation")

    ae = sum(config.adult_equivalent[m["age_band"]] for m in members)
    daily_use = {
        slug: per_adult * ae * math.exp(rng.gauss(0, 0.2))
        for slug, per_adult in sorted(config.daily_use_per_adult_week.items())
        if slug in catalog.products
    }
    # Fruit: the household's own mix (a Dirichlet draw) of the week's fruit grams.
    fruits = catalog.fruits()
    shares = [rng.gammavariate(1.5, 1) for _ in fruits]
    for slug, s in zip(fruits, shares, strict=True):
        daily_use[slug] = config.fruit_per_adult_week_g * ae * s / sum(shares)
    taste = {
        r["slug"]: (1.0 if r["cuisine"] in cuisines else -0.5) + rng.gauss(0, 1)
        for r in catalog.recipes
    }

    body = {
        "society_id": society_id,
        "address": {"tower_id": tower, "flat": flat, "location": None},
        "diet": diet,
        "cuisine_tags": cuisines,
        "weekday_cook_minutes": weekday,
        "weekend_cook_minutes": weekend,
        "no_cook_days": no_cook_days,
        "budget_paise": budget,
        "members": members,
        "consents": [{"purpose": p, "version": CONSENT_VERSION, "granted": True} for p in purposes],
    }
    rules = {
        "size": len(members),
        "diet": diet,
        "jain": jain,
        "diabetic": diabetic,
        "cuisines": cuisines,
        "weekday_cook_minutes": weekday,
        "weekend_cook_minutes": weekend,
        "no_cook_days": no_cook_days,
        # The Budget Lock is monthly; the planner works a week at a time.
        "weekly_budget_paise": None if budget is None else budget * 7 // 30,
    }
    return SimHousehold(
        key=f"h{n:04d}",
        phone=f"{PHONE_PREFIX}{n:05d}",
        society_id=society_id,
        cohort=c.name,
        locale=c.locale,
        body=body,
        rules=rules,
        fasts=fasts,
        no_onion_garlic_on_fasts=no_onion_garlic,
        adult_equivalents=round(ae, 2),
        daily_use=daily_use,
        taste=taste,
        accept_bias=c.accept_bias,
        skip_week=c.skip_week,
    )
