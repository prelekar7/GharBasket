"""Swapping one meal of the week and redrawing its lines (M3 §3.2).

The tests read the dishes, products and meals from the week the planner generated and from the
seed catalog, never by name, so a bigger catalog (or a better planner) does not break them."""

from collections.abc import Iterable

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.subscriptions.models import EditFeedback
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import MEENA, NOW, PRIYA, as_user, subscribe
from .test_edits import CUTOFF, events, generate, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

NAVRATRI = "2026-10-14"  # Wednesday: a Navratri fast, without onion or garlic, in the seed
Meal = tuple[str, str]  # (date, slot)


def meal(box: dict, at: Meal) -> dict:
    on, slot = at
    day = next(d for d in box["meal_plan"]["days"] if d["date"] == on)
    return next(m for m in day["meals"] if m["slot"] == slot)


def qty(box: dict) -> dict[str, tuple[int, str]]:
    return {line["slug"]: (line["qty"], line["reason_code"]) for line in box["lines"]}


async def put_meal(
    api: httpx2.AsyncClient, auth: dict, box: dict, at: Meal, **body
) -> httpx2.Response:
    on, slot = at
    return await api.put(
        f"/v1/cycles/{box['id']}/meals",
        json={"date": on, "slot": slot, **body},
        headers={**auth, "If-Match": f'"{box["version"]}"'},
    )


async def menu(db: AsyncSession) -> catalog_api.PlanCatalog:
    catalog = await catalog_api.plan_catalog(db, SEED_HUB_ID)
    await db.commit()
    return catalog


def first[T](found: Iterable[T], what: str) -> T:
    """The first of `found`; the test cannot run without one, so it fails saying what is missing."""
    item = next(iter(found), None)
    if item is None:
        pytest.fail(f"the seed catalog or the generated week has no {what}")
    return item


def mains(catalog: catalog_api.PlanCatalog, slot: str) -> list[catalog_api.PlanRecipe]:
    """The dishes a customer may pick for `slot`: mains with a price at the hub."""
    return [
        r
        for r in catalog.recipes
        if not r.is_side and r.cost_per_serving_paise is not None and slot in r.slots
    ]


def planned(box: dict, catalog: catalog_api.PlanCatalog) -> dict[Meal, str]:
    """Every meal of the week that has a dish, with the dish's slug, in date and slot order."""
    slugs = {str(r.id): r.slug for r in catalog.recipes}
    return {
        (day["date"], m["slot"]): slugs[m["recipe_id"]]
        for day in box["meal_plan"]["days"]
        for m in day["meals"]
        if m["recipe_id"]
    }


def own_products(box: dict) -> dict[str, str]:
    """Meal-plan lines that a single planned dish brings (no other dish of the week uses them):
    product id -> that dish's slug."""
    return {
        line["product_id"]: line["reason_params"]["recipes"][0]
        for line in box["lines"]
        if line["reason_code"] == "meal_plan" and len(line["reason_params"]["recipes"]) == 1
    }


def new_dish(
    catalog: catalog_api.PlanCatalog, box: dict, slot: str
) -> tuple[catalog_api.PlanRecipe, set[str]]:
    """A veg dish for `slot` (Priya's household is veg) that needs products the box does not
    have yet, with those products' slugs. Fruit is left out: it is always a daily-use line."""
    by_slug = {r.slug: r for r in catalog.recipes}
    on_box = {line["product_id"] for line in box["lines"]}

    def brings(recipe: catalog_api.PlanRecipe) -> set[str]:
        products = (
            catalog.products[i.product_id]
            for r in (recipe, *(by_slug[s] for s in recipe.sides if s in by_slug))
            for i in r.ingredients
            if not i.optional
        )
        return {
            p.slug
            for p in products
            if str(p.id) not in on_box and p.price_paise is not None and p.kind != "fruit"
        }

    return first(
        ((r, brings(r)) for r in mains(catalog, slot) if r.diet == "veg" and brings(r)),
        f"veg {slot} dish that brings a product the box does not have",
    )


@time_machine.travel(NOW, tick=False)
async def test_every_set_value_redraws_the_meal_and_restores_the_plan(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    await subscribe(api, priya)
    await subscribe(api, meena)
    await generate(api)
    catalog = await menu(db_session)
    start = (await api.get("/v1/cycles/current", headers=priya)).json()
    # The first lunch of the week that has a dish.
    at = first((m for m in planned(start, catalog) if m[1] == "lunch"), "planned lunch")
    dish = meal(start, at)["recipe_id"]
    assert meal(start, at)["source"] == "plan"
    in_week = {m["recipe_id"] for d in start["meal_plan"]["days"] for m in d["meals"]}

    # Refusals: stale or missing If-Match, someone else's week, a meal that is not there,
    # a dish off the menu for that slot, a side, and a dish against the household's rules.
    breakfast_only = first(
        (r for r in mains(catalog, "breakfast") if "lunch" not in r.slots), "breakfast-only dish"
    )
    side = first((r for r in catalog.recipes if r.is_side), "side")
    not_veg = first((r for r in mains(catalog, "lunch") if r.diet != "veg"), "non-veg lunch")
    stale = await api.put(
        f"/v1/cycles/{start['id']}/meals",
        json={"date": at[0], "slot": "lunch"},
        headers={**priya, "If-Match": '"7"'},
    )
    missing = await api.put(
        f"/v1/cycles/{start['id']}/meals", json={"date": at[0], "slot": "lunch"}, headers=priya
    )
    strangers = await put_meal(api, meena, start, at)
    outside = await put_meal(api, priya, start, at, date="2026-10-20")
    no_slot = await put_meal(api, priya, start, at, slot="snack")
    wrong_slot = await put_meal(
        api, priya, start, at, set="recipe", recipe_id=str(breakfast_only.id)
    )
    a_side = await put_meal(api, priya, start, at, set="recipe", recipe_id=str(side.id))
    diet = await put_meal(api, priya, start, at, set="recipe", recipe_id=str(not_veg.id))
    assert (stale.status_code, stale.json()["code"]) == (412, "subscriptions.version_conflict")
    assert (missing.status_code, missing.json()["code"]) == (428, "request.precondition_required")
    assert (strangers.status_code, strangers.json()["code"]) == (404, "subscriptions.no_cycle")
    assert outside.json()["code"] == no_slot.json()["code"] == "subscriptions.meal_unknown"
    assert wrong_slot.json()["code"] == a_side.json()["code"] == "subscriptions.recipe_unknown"
    assert (diet.status_code, diet.json()["code"], diet.json()["rules"]) == (
        422,
        "subscriptions.recipe_not_allowed",
        ["diet"],
    )
    # Meena's household is Jain and fasts through Navratri without onion or garlic.
    not_jain = first(
        (
            r
            for r in mains(catalog, "breakfast")
            if r.diet == "veg" and not r.rules["jain_ok"] and not r.rules["fasting_ok"]
        ),
        "veg breakfast with onion or garlic that is not a fasting dish",
    )
    meena_box = (await api.get("/v1/cycles/current", headers=meena)).json()
    fast = await put_meal(
        api, meena, meena_box, (NAVRATRI, "breakfast"), set="recipe", recipe_id=str(not_jain.id)
    )
    assert (fast.status_code, fast.json()["rules"]) == (422, ["jain", "fasting"])

    # next (the default): the cheapest allowed lunch not in the week, within 30 weekday minutes.
    swapped = await put_meal(api, priya, start, at)
    assert swapped.status_code == 200, swapped.text
    box = swapped.json()
    chosen = meal(box, at)
    assert box["version"] == 2 and swapped.headers["etag"] == '"2"'
    assert chosen["source"] == "customer" and chosen["recipe_id"] not in in_week

    # recipe: a dish that needs products the box lacks brings new lines for them at today's
    # price, owned by that dish alone.
    picked, brings = new_dish(catalog, box, "lunch")
    box = (await put_meal(api, priya, box, at, set="recipe", recipe_id=str(picked.id))).json()
    lines = {line["slug"]: line for line in box["lines"]}
    assert meal(box, at)["recipe_id"] == str(picked.id)
    for slug in brings:
        assert lines[slug]["source"] == "customer" and lines[slug]["reason_code"] == "meal_plan"
        assert lines[slug]["reason_params"] == {"recipes": [picked.slug]}

    # tiffin and no-cook: the meal brings nothing, so its own lines go.
    box = (await put_meal(api, priya, box, at, set="tiffin")).json()
    tiffin = meal(box, at)
    assert (tiffin["recipe_id"], tiffin["tiffin"], tiffin["source"]) == (None, True, "customer")
    assert not brings & qty(box).keys()
    box = (await put_meal(api, priya, box, at, set="no_cook")).json()
    assert meal(box, at)["tiffin"] is False

    # planned: back to the planner's dish and, apart from daily use, to the generated lines.
    box = (await put_meal(api, priya, box, at, set="planned")).json()
    assert meal(box, at)["recipe_id"] == dish
    assert box["version"] == 6
    before, after = qty(start), qty(box)
    assert {s: q for s, q in after.items() if q[1] != "daily_use"} == {
        s: q for s, q in before.items() if q[1] != "daily_use"
    }
    assert all(after[s][0] >= q[0] for s, q in before.items() if q[1] == "daily_use")

    # One meal row per call and no product rows: the ranker learns only the customer's picks.
    rows = (
        (await db_session.execute(select(EditFeedback).order_by(EditFeedback.at, EditFeedback.id)))
        .scalars()
        .all()
    )
    assert [r.action for r in rows] == [
        "meal_swapped",
        "meal_swapped",
        "meal_tiffin",
        "meal_no_cook",
        "meal_restored",
    ]
    assert {(r.product_id, str(r.meal_date), r.slot, r.at) for r in rows} == {
        (None, at[0], "lunch", NOW)
    }
    chain = [dish, chosen["recipe_id"], str(picked.id), None, None]
    assert [str(r.recipe_id) if r.recipe_id else None for r in rows] == chain
    assert [str(r.substitute_recipe_id) if r.substitute_recipe_id else None for r in rows] == [
        *chain[1:],
        dish,
    ]

    # On an approved week the redrawn lines go to M4 again.
    approved = await api.post(
        f"/v1/cycles/{box['id']}/approve", headers={**priya, **key(), "If-Match": '"6"'}
    )
    box = approved.json()
    after_approval = await put_meal(api, priya, box, at, set="tiffin")
    assert after_approval.json()["status"] == "approved"
    assert len(await events(db_session, "subscriptions.box_approved")) == 2

    # After the cut-off the week is fixed.
    await db_session.commit()
    with time_machine.travel(CUTOFF, tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        late = await put_meal(api, priya, after_approval.json(), at, set="planned")
    assert (late.status_code, late.json()["code"]) == (409, "subscriptions.cutoff_passed")


@time_machine.travel(NOW, tick=False)
async def test_a_swap_keeps_what_the_customer_added_and_daily_use(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    catalog = await menu(db_session)
    box = (await api.get("/v1/cycles/current", headers=priya)).json()
    by_id = {line["product_id"]: line for line in box["lines"]}
    owner = own_products(box)
    # Two products each brought by a single dish: one the customer swaps for a substitute that
    # is not in the box yet, the other she turns into her own line of 3 steps.
    swap = None
    for product_id in owner:
        offered = (await api.get(f"/v1/products/{product_id}/substitutes", headers=priya)).json()
        fresh = [s for s in offered["items"] if s["product_id"] not in by_id]
        if fresh:
            swap = (by_id[product_id], fresh[0])
            break
    if swap is None:
        pytest.fail("no product of a single planned dish has a substitute outside the box")
    replaced, substitute = swap
    mine = by_id[first((p for p in owner if p != replaced["product_id"]), "second own product")]
    edited = await api.patch(
        f"/v1/cycles/{box['id']}/lines",
        json={
            "ops": [
                {"op": "remove", "product_id": mine["product_id"]},
                {"op": "add", "product_id": mine["product_id"], "qty": 3},
                {
                    "op": "substitute",
                    "product_id": replaced["product_id"],
                    "substitute_id": substitute["product_id"],
                },
            ]
        },
        headers={**priya, "If-Match": '"1"'},
    )
    start = edited.json()
    assert qty(start)[mine["slug"]] == (3, "added_by_you")
    assert qty(start)[substitute["slug"]] == (replaced["qty"], "swapped_by_you")

    # Every meal of the two dishes becomes a tiffin.
    dishes = {owner[replaced["product_id"]], owner[mine["product_id"]]}
    tiffins = [at for at, slug in planned(start, catalog).items() if slug in dishes]
    box = start
    for at in tiffins:
        answer = await put_meal(api, priya, box, at, set="tiffin")
        assert answer.status_code == 200, answer.text
        box = answer.json()
    after = qty(box)
    assert after[mine["slug"]] == (3, "added_by_you")
    assert replaced["slug"] not in after
    assert substitute["slug"] not in after  # the substitute counts as the product it replaced
    assert {s: q for s, q in after.items() if q[1] == "daily_use"} == {
        s: q for s, q in qty(start).items() if q[1] == "daily_use"
    }
    # One meal row per tiffin; the product rows are the line edit's own.
    actions = (await db_session.execute(select(EditFeedback.action))).scalars().all()
    assert sorted(actions) == ["added", *["meal_tiffin"] * len(tiffins), "removed", "swapped"]


@time_machine.travel(NOW, tick=False)
async def test_the_budget_lock_refuses_only_a_swap_that_raises_the_total(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    catalog = await menu(db_session)
    start = (await api.get("/v1/cycles/current", headers=priya)).json()
    locked = await api.put(
        "/v1/budget-lock",
        json={"month": "2026-10", "budget_paise": 50_000},
        headers={**priya, **key()},
    )
    assert locked.status_code == 200 and start["total_paise"] > 50_000  # already over

    # A dish that brings a product of its own (the one planned on the fewest meals), made a
    # tiffin at each of its meals: every swap lowers the total or keeps it, and goes through
    # even over the lock; once the last of them is a tiffin, its product is gone.
    week = planned(start, catalog)
    dish = first(
        sorted(
            set(own_products(start).values()),
            key=lambda slug: (list(week.values()).count(slug), slug),
        ),
        "planned dish with a product of its own",
    )
    meals = [at for at, slug in week.items() if slug == dish]
    box = start
    for at in meals:
        answer = await put_meal(api, priya, box, at, set="tiffin")
        assert answer.status_code == 200 and answer.json()["total_paise"] <= box["total_paise"]
        box = answer.json()
    assert box["total_paise"] < start["total_paise"]
    last = meals[-1]
    same = await put_meal(api, priya, box, last, set="no_cook")
    assert same.status_code == 200 and same.json()["total_paise"] == box["total_paise"]
    box = same.json()
    # Raising it does not: the planned dish back, or a dish that needs new products.
    picked, _ = new_dish(catalog, box, last[1])
    for refused in (
        await put_meal(api, priya, box, last, set="planned"),
        await put_meal(api, priya, box, last, set="recipe", recipe_id=str(picked.id)),
    ):
        assert (refused.status_code, refused.json()["code"]) == (
            422,
            "subscriptions.budget_exceeded",
        )
        assert refused.json()["over_paise"] > 0
    unchanged = (await api.get(f"/v1/cycles/{box['id']}", headers=priya)).json()
    assert unchanged["version"] == box["version"] and unchanged["lines"] == box["lines"]

    off = await api.put(
        "/v1/budget-lock",
        json={"month": "2026-10", "budget_paise": None},
        headers={**priya, **key()},
    )
    assert off.status_code == 204
    restored = await put_meal(api, priya, box, last, set="planned")
    assert restored.status_code == 200 and restored.json()["total_paise"] > box["total_paise"]
    assert meal(restored.json(), last)["recipe_id"] == meal(start, last)["recipe_id"]
