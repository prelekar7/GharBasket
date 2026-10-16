"""Fill the local database with synthetic people (one for each role) and societies (ADR-0008).

    just seed

Every row is `data_source='synthetic'`, so it never counts in real metrics. Running it again
changes nothing. It writes through the module services, like the simulator will, so the same
rules hold as for a real sign-in: a new phone becomes a customer, staff roles are audit-logged.

The phones +91 98000 01001 to 01013 are reserved for this seed. Sign in as any of them locally
with `dev_phone` (`POST /v1/auth/exchange`, APP_ENV=dev). The societies are made-up names in real
Kharghar, Kamothe and Panvel sectors, with approximate boundaries; five of the people have a
household there, created through the same onboarding service as `POST /v1/households`. Each live
society has a verified captain partner (M8) who lives there, verified like a real one; the seed
delivers its events at the end, so M9 appoints her and M1 grants her `captain:<society>` role. The
catalog (categories and products with English, Hindi and Marathi names) comes from
data/seeds/catalog/*.json. The seed hub gets one morning box slot and every seed society with a
hub delivers on Tuesdays (M3/M4), so `just sim` and the weekly job have somewhere to deliver.
The seed hub itself (M6, ADR-0026 §5) is Kharghar, with a service area around the seed societies
and one default vendor, so every hub id above points at a real `hub_ops.hubs` row.
Later module PRs add their rows here and use the hub, society and NGO ids below.

Only for the local Docker database: shared databases (staging) are always refused.
"""

import argparse
import asyncio
import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import NUTRITION_SOURCES, Product, Recipe
from gharbasket.modules.catalog.pricing import save_price_list
from gharbasket.modules.catalog.recipes import (
    IngredientData,
    RecipeData,
    recompute_rules,
    save_festival,
    save_recipe,
)
from gharbasket.modules.catalog.schemas import PriceItem
from gharbasket.modules.catalog.service import (
    CategoryData,
    ProductData,
    save_category,
    save_product,
)
from gharbasket.modules.community.service import SocietyData, save_society
from gharbasket.modules.hub_ops import service as hub_service
from gharbasket.modules.hub_ops.models import Hub
from gharbasket.modules.hub_ops.schemas import GeoPolygon, HubIn, HubPatch, VendorIn
from gharbasket.modules.identity.households import create_household, household_of
from gharbasket.modules.identity.schemas import HouseholdIn
from gharbasket.modules.identity.service import find_or_create_user, grant_role
from gharbasket.modules.orders import service as orders_service
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import service as partners_service
from gharbasket.modules.registry import load_modules
from gharbasket.modules.subscriptions import service as subscriptions_service
from gharbasket.platform import outbox
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import LatLng
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[5]
CATALOG_SEED = REPO_ROOT / "data" / "seeds" / "catalog"

# Fixed ids, so every machine gets the same world and later seeds can point at these rows.
SEED_HUB_ID = uuid.UUID("0199a000-0000-7000-8000-00000000a001")  # Kharghar hub
SEED_SOCIETY_ID = uuid.UUID("0199a000-0000-7000-8000-00000000b001")  # Sunrise Towers
SEED_NGO_ID = uuid.UUID("0199a000-0000-7000-8000-00000000c001")


@dataclass(frozen=True)
class Person:
    phone: str
    name: str
    locale: str = "en-IN"
    senior_mode: bool = False
    # Customer comes with every sign-up; these are the extra staff roles and their scope.
    roles: tuple[tuple[str, uuid.UUID | None], ...] = field(default=())


PEOPLE = (
    Person("+919800001001", "Priya Sharma"),
    Person("+919800001002", "Sunita Patil", locale="mr-IN"),
    Person("+919800001003", "Ramesh Kulkarni", locale="mr-IN", senior_mode=True),
    # Meena is a captain through M9's appointment (CAPTAINS below), not a role granted here.
    Person("+919800001004", "Meena Jadhav", locale="hi-IN"),
    Person(
        "+919800001005", "Lata Shinde", locale="mr-IN", roles=(("kitchen_partner", SEED_HUB_ID),)
    ),
    Person("+919800001006", "Arjun Pawar", locale="hi-IN", roles=(("rider", SEED_HUB_ID),)),
    Person("+919800001007", "Kavita More", locale="mr-IN", roles=(("packer", SEED_HUB_ID),)),
    Person("+919800001008", "Vikram Desai", roles=(("hub_manager", SEED_HUB_ID),)),
    Person("+919800001009", "Sanjay Mehta", roles=(("franchise_owner", SEED_HUB_ID),)),
    Person("+919800001010", "Asha Fernandes", roles=(("ngo_coordinator", SEED_NGO_ID),)),
    Person("+919800001011", "Neha Iyer", roles=(("admin", None),)),
    Person("+919800001012", "Savita Naik", locale="mr-IN"),
    Person("+919800001013", "Rekha Joshi", roles=(("catalog_editor", None),)),
)


def _square(lat: float, lng: float) -> list[tuple[float, float]]:
    """About 130 m x 150 m around a centre: enough for a demo boundary."""
    d_lat, d_lng = 0.0006, 0.0007
    return [
        (lat - d_lat, lng - d_lng),
        (lat - d_lat, lng + d_lng),
        (lat + d_lat, lng + d_lng),
        (lat + d_lat, lng - d_lng),
    ]


def _wings(*letters: str) -> list[dict[str, str]]:
    return [{"id": letter.lower(), "label": f"{letter} wing"} for letter in letters]


def _society_id(n: int) -> uuid.UUID:
    return uuid.UUID(f"0199a000-0000-7000-8000-00000000b{n:03d}")


SOCIETIES = (
    SocietyData(
        id=SEED_SOCIETY_ID,
        slug="sunrise-towers-kharghar",
        name="Sunrise Towers",
        area="Sector 20, Kharghar",
        boundary=_square(19.0395, 73.0675),
        towers=_wings("A", "B", "C"),
        flats=240,
        unlock_threshold=20,
        status="live",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(2),
        slug="green-valley-heights-kharghar",
        name="Green Valley Heights",
        area="Sector 35, Kharghar",
        boundary=_square(19.0265, 73.0590),
        towers=_wings("A", "B"),
        flats=160,
        unlock_threshold=25,
        status="unlocking",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(3),
        slug="shree-ganesh-chs-kharghar",
        name="Shree Ganesh CHS",
        area="Sector 12, Kharghar",
        boundary=_square(19.0430, 73.0720),
        towers=[{"id": "main", "label": "Main building"}],
        flats=48,
        unlock_threshold=15,
        status="unlocking",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(4),
        slug="panchavati-residency-kamothe",
        name="Panchavati Residency",
        area="Sector 7, Kamothe",
        boundary=_square(19.0190, 73.0960),
        towers=_wings("A", "B", "C", "D"),
        flats=320,
        unlock_threshold=20,
        status="prospect",
        hub_id=SEED_HUB_ID,
    ),
    SocietyData(
        id=_society_id(5),
        slug="krishna-kunj-new-panvel",
        name="Krishna Kunj",
        area="Sector 17, New Panvel",
        boundary=_square(19.0005, 73.1185),
        towers=_wings("A", "B"),
        flats=96,
        unlock_threshold=20,
        status="live",
        hub_id=SEED_HUB_ID,
    ),
    # No boundary drawn yet: addresses are accepted anywhere until a hub manager draws one.
    SocietyData(
        id=_society_id(6),
        slug="sai-darshan-apartments-panvel",
        name="Sai Darshan Apartments",
        area="Old Panvel",
        boundary=None,
        towers=[],
        flats=None,
        unlock_threshold=20,
        status="prospect",
        hub_id=None,
    ),
)


SEED_HUB = HubIn(
    slug="kharghar",
    name="Kharghar hub",
    address="Plot 12, Sector 19, Kharghar, Navi Mumbai 410210",
    location=LatLng(lat=19.0330, lng=73.0297),
    # Around every seed society with a hub (Kharghar, Kamothe, New Panvel).
    service_area=GeoPolygon(
        type="Polygon",
        coordinates=[
            [[73.02, 18.99], [73.13, 18.99], [73.13, 19.05], [73.02, 19.05], [73.02, 18.99]]
        ],
    ),
)
SEED_VENDOR = VendorIn(
    name="Shinde Traders",
    kind="apmc_trader",
    phone="+919800002001",
    is_default=True,
    notes="APMC Vashi, vegetables",
)


def _consents(*extra: str) -> list[dict[str, object]]:
    purposes = ("service_delivery", "personalisation", *extra)
    return [{"purpose": purpose, "version": "v1", "granted": True} for purpose in purposes]


def _adult(sex: str, **rules: object) -> dict[str, object]:
    return {"age_band": "18_59", "sex": sex, "fasting_rules": rules}


# Onboarding requests (POST /v1/households bodies) of the seeded customers, by phone.
HOUSEHOLDS: dict[str, dict[str, object]] = {
    "+919800001001": {  # Priya: family of four, Sunrise Towers
        "society_id": str(SEED_SOCIETY_ID),
        "address": {
            "tower_id": "a",
            "flat": "A-1203",
            "location": {"lat": 19.0396, "lng": 73.0676},
        },
        "diet": "veg",
        "cuisine_tags": ["maharashtrian", "north_indian"],
        "weekday_cook_minutes": 30,
        "weekend_cook_minutes": 60,
        "no_cook_days": [6],
        "budget_paise": 600_000,
        "members": [
            _adult("f", fasts=["shravan_mondays"]),
            _adult("m"),
            {"age_band": "5_17", "sex": "f"},
            {"age_band": "under_5", "sex": "m"},
        ],
        "consents": _consents("marketing"),
    },
    "+919800001002": {  # Sunita: couple, eggs on, Green Valley Heights, no pin placed
        "society_id": str(_society_id(2)),
        "address": {"tower_id": "b", "flat": "B-504", "location": None},
        "diet": "egg",
        "cuisine_tags": ["maharashtrian"],
        "weekday_cook_minutes": 45,
        "weekend_cook_minutes": 90,
        "no_cook_days": [],
        "budget_paise": None,
        "members": [_adult("f", fasts=["ekadashi"]), _adult("m")],
        "consents": _consents(),
    },
    "+919800001003": {  # Ramesh: senior couple with health personalisation, Krishna Kunj
        "society_id": str(_society_id(5)),
        "address": {"tower_id": "a", "flat": "A-101", "location": {"lat": 19.0005, "lng": 73.1185}},
        "diet": "veg",
        "cuisine_tags": ["maharashtrian", "gujarati"],
        "weekday_cook_minutes": 20,
        "weekend_cook_minutes": 30,
        "no_cook_days": [],
        "budget_paise": 400_000,
        "members": [
            {"age_band": "60_plus", "sex": "m", "health_tags": ["diabetes", "low_oil"]},
            {"age_band": "60_plus", "sex": "f", "health_tags": ["high_bp"]},
        ],
        "consents": _consents("health_personalisation"),
    },
    "+919800001004": {  # Meena (the captain): Jain household in her own society, Sunrise Towers
        "society_id": str(SEED_SOCIETY_ID),
        "address": {"tower_id": "b", "flat": "B-402", "location": None},
        "diet": "veg",
        "cuisine_tags": ["gujarati"],
        "weekday_cook_minutes": 40,
        "weekend_cook_minutes": 60,
        "no_cook_days": [],
        "budget_paise": 500_000,
        "members": [
            _adult("f", jain=True, fasts=["navratri"], no_onion_garlic_on_fast_days=True),
            _adult("m", jain=True),
            {"age_band": "5_17", "sex": "m", "fasting_rules": {"jain": True}},
        ],
        "consents": _consents(),
    },
    "+919800001012": {  # Savita (Krishna Kunj's captain): she lives there, alone
        "society_id": str(_society_id(5)),
        "address": {"tower_id": "b", "flat": "B-204", "location": None},
        "diet": "veg",
        "cuisine_tags": ["maharashtrian"],
        "weekday_cook_minutes": 30,
        "weekend_cook_minutes": 45,
        "no_cook_days": [],
        "budget_paise": None,
        "members": [_adult("f")],
        "consents": _consents(),
    },
}

# One verified captain partner per live seed society, each living there (M8 build order (a),
# M9 §3.2): (phone, society, display name). Seeded before applications exist; the seed publishes
# partners.verified for each, as a real verification would.
CAPTAINS = (
    ("+919800001004", SEED_SOCIETY_ID, "Meena tai"),
    ("+919800001012", _society_id(5), "Savita tai"),
)


@dataclass(frozen=True)
class Seeded:
    phone: str
    name: str
    roles: tuple[str, ...]
    created: bool


async def seed(session: AsyncSession) -> list[Seeded]:
    """Create or refresh the synthetic people. Commits nothing; the caller does."""
    seeded: list[Seeded] = []
    for person in PEOPLE:
        user, created = await find_or_create_user(session, person.phone)
        user.name = person.name
        user.locale = person.locale
        user.senior_mode = person.senior_mode
        user.data_source = "synthetic"
        await session.flush()
        for role, scope_id in person.roles:
            try:
                await grant_role(
                    session,
                    phone_e164=person.phone,
                    role=role,
                    scope_id=scope_id,
                    granted_by=None,
                )
            except ProblemError as problem:
                if problem.code != "identity.role_exists":
                    raise
        roles = ("customer", *(role for role, _ in person.roles))
        seeded.append(Seeded(person.phone, person.name, roles, created))
    return seeded


async def seed_hubs(session: AsyncSession) -> list[tuple[str, bool]]:
    """Create or refresh the seed hub, and give it its default vendor once: (slug, created).

    Commits nothing. A refresh puts the hub back as SEED_HUB describes it (version + 1 only if
    something had changed); an existing vendor of that name is kept as it is.
    """
    hub = await session.get(Hub, SEED_HUB_ID)
    created = hub is None
    if hub is None:
        hub = await hub_service.create_hub(
            session, SEED_HUB, hub_id=SEED_HUB_ID, data_source="synthetic"
        )
    else:
        fields = SEED_HUB.model_dump(
            exclude={"slug", "timings", "procurement_buffer_pct", "pre_live_box_fee_paise"}
        )
        await hub_service.update_hub(session, hub, HubPatch.model_validate(fields))
    vendors = await hub_service.list_vendors(session, hub.id, None)
    if not any(vendor.name.lower() == SEED_VENDOR.name.lower() for vendor in vendors):
        await hub_service.create_vendor(session, hub, SEED_VENDOR)
    return [(hub.slug, created)]


async def seed_societies(session: AsyncSession) -> list[tuple[str, bool]]:
    """Create or refresh the synthetic societies: (slug, created) each. Commits nothing."""
    saved: list[tuple[str, bool]] = []
    for data in SOCIETIES:
        society, created = await save_society(session, replace(data, data_source="synthetic"))
        saved.append((society.slug, created))
    return saved


async def seed_households(session: AsyncSession) -> list[tuple[str, bool]]:
    """Onboard the seeded customers who have no household yet: (phone, created) each.

    Needs the people and societies first. An existing household is kept as it is.
    """
    done: list[tuple[str, bool]] = []
    for phone, request in HOUSEHOLDS.items():
        user, _ = await find_or_create_user(session, phone)
        created = await household_of(session, user.id) is None
        if created:
            body = HouseholdIn.model_validate(request)
            await create_household(session, user.id, body, data_source="synthetic")
        done.append((phone, created))
    return done


async def seed_partners(session: AsyncSession) -> list[tuple[str, bool]]:
    """Verify the seeded captains (M8), publishing `partners.verified` with the society each
    lives in, so M9 appoints her: (phone, created) each. Needs the people, societies and
    households first. An existing partner record is kept as it is."""
    hubs = {society.id: society.hub_id for society in SOCIETIES}
    done: list[tuple[str, bool]] = []
    for phone, society_id, display_name in CAPTAINS:
        user, _ = await find_or_create_user(session, phone)
        hub_id = hubs[society_id]
        assert hub_id is not None, "a seeded captain's society needs a hub"
        created = await partners_api.partner_for_user(session, user.id, "captain") is None
        if created:
            await partners_service.verify(
                session,
                user_id=user.id,
                kind="captain",
                hub_id=hub_id,
                display_name=display_name,
                requested_society_id=society_id,
                data_source="synthetic",
            )
        done.append((phone, created))
    return done


async def _kept(session: AsyncSession, model: Any, slug: str, refresh: bool) -> Any:
    """The existing row the seed leaves alone: every existing one, or with `refresh` only those
    edited in the console (`updated_by` set). None when the seed should write the row."""
    row = (await session.execute(select(model).where(model.slug == slug))).scalar_one_or_none()
    return row if row is not None and (row.updated_by is not None or not refresh) else None


async def seed_catalog(session: AsyncSession, *, refresh: bool = False) -> list[tuple[str, bool]]:
    """Add data/seeds/catalog: categories, products, the seed hub's price list, recipes and
    festival days.

    Products and recipes are only added (by slug); with `refresh` the seed files' values are
    re-applied to the rows nobody edited in the console (M2 §3.3). Returns (slug, created) for
    each product.
    """
    categories = json.loads((CATALOG_SEED / "categories.json").read_text())["categories"]
    for category in categories:
        await save_category(session, CategoryData(**category))
    saved: list[tuple[str, bool]] = []
    ids: dict[str, uuid.UUID] = {}
    nutrition = {
        row["slug"]: {
            **row["per_100g"],
            "unit_g": row.get("unit_g"),
            "source": NUTRITION_SOURCES[row["source"]["name"]],
            "ref": {key: value for key, value in row["source"].items() if key != "name"},
        }
        for row in json.loads((CATALOG_SEED / "nutrition.json").read_text())["items"]
    }
    for item in json.loads((CATALOG_SEED / "products.json").read_text())["products"]:
        step = item.pop("step")
        data = ProductData(
            **{key: value for key, value in item.items() if key not in {"tags", "synonyms"}},
            step_qty=step["qty"],
            step_unit=step["unit"],
            tags=tuple(item["tags"]),
            synonyms=tuple(item["synonyms"]),
            data_source="synthetic",
            nutrition=nutrition.get(item["slug"]),
        )
        if kept := await _kept(session, Product, data.slug, refresh):
            saved.append((kept.slug, False))
            ids[kept.slug] = kept.id
            continue
        product, created = await save_product(session, data)
        saved.append((product.slug, created))
        ids[product.slug] = product.id

    prices = json.loads((CATALOG_SEED / "prices.json").read_text())
    await save_price_list(
        session,
        SEED_HUB_ID,
        date.fromisoformat(prices["valid_from"]),
        [
            PriceItem(
                product_id=ids[item["slug"]],
                price_paise=item["price_paise"],
                mrp_paise=item["mrp_paise"],
            )
            for item in prices["items"]
        ],
        published_by=None,
        data_source="synthetic",
    )

    recipes = json.loads((CATALOG_SEED / "recipes.json").read_text())["recipes"]
    for recipe in sorted(recipes, key=lambda r: not r.get("is_side", False)):  # sides first
        if await _kept(session, Recipe, recipe["slug"], refresh):
            continue
        await save_recipe(
            session,
            RecipeData(
                slug=recipe["slug"],
                names=recipe["names"],
                cuisine=recipe["cuisine"],
                slots=tuple(recipe["slots"]),
                cook_minutes=recipe["cook_minutes"],
                servings=recipe["servings"],
                fasting_ok=recipe["fasting_ok"],
                diabetic_ok=recipe["diabetic_ok"],
                tags=tuple(recipe["tags"]),
                is_side=recipe.get("is_side", False),
                sides=tuple(recipe.get("sides", ())),
                ingredients=tuple(
                    IngredientData(
                        product=i["product"],
                        qty=Decimal(str(i["qty"])),
                        unit=i["unit"],
                        optional=i["optional"],
                    )
                    for i in recipe["ingredients"]
                ),
                data_source="synthetic",
            ),
        )
    if refresh:  # refreshed tags may change the rules of recipes edited in the console
        await recompute_rules(session, list(ids.values()), None)
    for festival in json.loads((CATALOG_SEED / "festivals.json").read_text())["festivals"]:
        await save_festival(
            session,
            day=date.fromisoformat(festival["date"]),
            code=festival["code"],
            region=festival["region"],
            names=festival["names"],
            rules=festival["rules"],
            verified=festival["verified"],
        )
    return saved


SEED_SLOT_LABEL = "Morning 7-9"
SEED_DELIVERY_WEEKDAY = 1  # Tuesday


async def seed_delivery(session: AsyncSession) -> tuple[uuid.UUID, int]:
    """One morning box slot at the seed hub, and Tuesday delivery for each seed society with a
    hub (M3/M4). Returns (slot id, societies set). Existing rows are kept."""
    from datetime import time

    from gharbasket.modules.orders.models import Slot

    slot = (
        await session.execute(
            select(Slot).where(Slot.hub_id == SEED_HUB_ID, Slot.label == SEED_SLOT_LABEL)
        )
    ).scalar_one_or_none()
    if slot is None:
        slot = await orders_service.create_slot(
            session,
            hub_id=SEED_HUB_ID,
            label=SEED_SLOT_LABEL,
            kind="box",
            start=time(7, 0),
            end=time(9, 0),
            capacity=120,
        )
    societies = 0
    for data in SOCIETIES:
        if data.hub_id is None:
            continue
        if not await subscriptions_service.delivery_days(session, data.id):
            await subscriptions_service.set_delivery_days(
                session, data.id, SEED_HUB_ID, [(SEED_DELIVERY_WEEKDAY, slot.id)]
            )
        societies += 1
    return slot.id, societies


@dataclass(frozen=True)
class Report:
    people: list[Seeded]
    societies: list[tuple[str, bool]]
    households: list[tuple[str, bool]]
    partners: list[tuple[str, bool]]
    products: list[tuple[str, bool]]
    delivery: tuple[uuid.UUID, int] | None = None
    hubs: list[tuple[str, bool]] = field(default_factory=list[tuple[str, bool]])


async def run(database_url: str, *, refresh: bool = False) -> Report:
    load_modules()  # the relay at the end needs every module's consumers
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            report = Report(
                hubs=await seed_hubs(session),
                people=await seed(session),
                societies=await seed_societies(session),
                households=await seed_households(session),
                partners=await seed_partners(session),
                products=await seed_catalog(session, refresh=refresh),
                delivery=await seed_delivery(session),
            )
            await session.commit()
            # Deliver the seed's events now, so M9 appoints the captains and M1 grants their role.
            while (await outbox.relay(session)).sent:
                pass
            return report
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill the local database with synthetic data.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-apply the catalog seed files to products and recipes nobody edited in the console",
    )
    args = parser.parse_args(argv)

    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    if not is_local_database(url):
        parser.error("seed data is only for the local Docker database")

    report = asyncio.run(run(url, refresh=args.refresh))
    for slug, created in report.hubs:
        print(f"hub {slug} ({SEED_HUB_ID}) {'new' if created else 'kept'}")
    seeded, societies = report.people, report.societies
    for person in seeded:
        state = "new" if person.created else "kept"
        print(f"{person.phone}  {person.name:<16} {', '.join(person.roles):<26} {state}")
    print(f"{len(seeded)} synthetic people; sign in with dev_phone (APP_ENV=dev)")
    for slug, created in societies:
        print(f"{slug:<32} {'new' if created else 'kept'}")
    print(f"{len(societies)} synthetic societies")
    for phone, created in report.households:
        print(f"{phone}  household {'new' if created else 'kept'}")
    print(f"{len(report.households)} synthetic households")
    for phone, created in report.partners:
        print(f"{phone}  captain partner {'new' if created else 'kept'}")
    print(f"{len(report.partners)} synthetic captain partners")
    new = sum(created for _, created in report.products)
    print(f"{len(report.products)} synthetic products ({new} new)")
    if report.delivery:
        print(f"delivery: slot {report.delivery[0]} on Tuesdays for {report.delivery[1]} societies")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
