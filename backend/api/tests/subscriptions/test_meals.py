"""Swapping one meal of the week and redrawing its lines (M3 §3.2)."""

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

WEDNESDAY = "2026-10-14"  # Priya's planned lunch: kobi-bhaji, the only dish with cabbage
SATURDAY = "2026-10-17"  # kobi-bhaji again


def meal(box: dict, on: str, slot: str) -> dict:
    day = next(d for d in box["meal_plan"]["days"] if d["date"] == on)
    return next(m for m in day["meals"] if m["slot"] == slot)


def qty(box: dict) -> dict[str, tuple[int, str]]:
    return {line["slug"]: (line["qty"], line["reason_code"]) for line in box["lines"]}


async def put_meal(api: httpx2.AsyncClient, auth: dict, box: dict, **body) -> httpx2.Response:
    return await api.put(
        f"/v1/cycles/{box['id']}/meals",
        json={"date": WEDNESDAY, "slot": "lunch", **body},
        headers={**auth, "If-Match": f'"{box["version"]}"'},
    )


async def recipe_ids(db: AsyncSession) -> dict[str, str]:
    catalog = await catalog_api.plan_catalog(db, SEED_HUB_ID)
    await db.commit()
    return {r.slug: str(r.id) for r in catalog.recipes}


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
    ids = await recipe_ids(db_session)
    start = (await api.get("/v1/cycles/current", headers=priya)).json()
    planned = meal(start, WEDNESDAY, "lunch")
    assert planned["recipe_id"] == ids["kobi-bhaji"] and planned["source"] == "plan"
    in_week = {m["recipe_id"] for d in start["meal_plan"]["days"] for m in d["meals"]}

    # Refusals: stale or missing If-Match, someone else's week, a meal that is not there,
    # a dish off the menu for that slot, a side, and a dish against the household's rules.
    stale = await api.put(
        f"/v1/cycles/{start['id']}/meals",
        json={"date": WEDNESDAY, "slot": "lunch"},
        headers={**priya, "If-Match": '"7"'},
    )
    missing = await api.put(
        f"/v1/cycles/{start['id']}/meals", json={"date": WEDNESDAY, "slot": "lunch"}, headers=priya
    )
    strangers = await put_meal(api, meena, start)
    outside = await put_meal(api, priya, start, date="2026-10-20")
    no_slot = await put_meal(api, priya, start, slot="snack")
    breakfast_only = await put_meal(api, priya, start, set="recipe", recipe_id=ids["kanda-poha"])
    side = await put_meal(api, priya, start, set="recipe", recipe_id=ids["chapati"])
    egg = await put_meal(api, priya, start, set="recipe", recipe_id=ids["egg-curry"])
    assert (stale.status_code, stale.json()["code"]) == (412, "subscriptions.version_conflict")
    assert (missing.status_code, missing.json()["code"]) == (428, "request.precondition_required")
    assert (strangers.status_code, strangers.json()["code"]) == (404, "subscriptions.no_cycle")
    assert outside.json()["code"] == no_slot.json()["code"] == "subscriptions.meal_unknown"
    assert breakfast_only.json()["code"] == side.json()["code"] == "subscriptions.recipe_unknown"
    assert (egg.status_code, egg.json()["code"], egg.json()["rules"]) == (
        422,
        "subscriptions.recipe_not_allowed",
        ["diet"],
    )
    # Meena's household is Jain and fasts through Navratri without onion or garlic.
    meena_box = (await api.get("/v1/cycles/current", headers=meena)).json()
    poha = await api.put(
        f"/v1/cycles/{meena_box['id']}/meals",
        json={
            "date": WEDNESDAY,
            "slot": "breakfast",
            "set": "recipe",
            "recipe_id": ids["kanda-poha"],
        },
        headers={**meena, "If-Match": '"1"'},
    )
    assert (poha.status_code, poha.json()["rules"]) == (422, ["jain", "fasting"])

    # next (the default): the cheapest allowed lunch not in the week, within 30 weekday minutes.
    swapped = await put_meal(api, priya, start)
    assert swapped.status_code == 200, swapped.text
    box = swapped.json()
    chosen = meal(box, WEDNESDAY, "lunch")
    assert box["version"] == 2 and swapped.headers["etag"] == '"2"'
    assert chosen["source"] == "customer" and chosen["recipe_id"] not in in_week

    # recipe: palak-paneer brings new lines for its own ingredients at today's price.
    box = (await put_meal(api, priya, box, set="recipe", recipe_id=ids["palak-paneer"])).json()
    lines = {line["slug"]: line for line in box["lines"]}
    assert meal(box, WEDNESDAY, "lunch")["recipe_id"] == ids["palak-paneer"]
    for slug in ("palak", "paneer"):
        assert lines[slug]["source"] == "customer" and lines[slug]["reason_code"] == "meal_plan"
        assert lines[slug]["reason_params"] == {"recipes": ["palak-paneer"]}

    # tiffin and no-cook: the meal brings nothing, so its own lines go.
    box = (await put_meal(api, priya, box, set="tiffin")).json()
    tiffin = meal(box, WEDNESDAY, "lunch")
    assert (tiffin["recipe_id"], tiffin["tiffin"], tiffin["source"]) == (None, True, "customer")
    assert "palak" not in qty(box) and "paneer" not in qty(box)
    box = (await put_meal(api, priya, box, set="no_cook")).json()
    assert meal(box, WEDNESDAY, "lunch")["tiffin"] is False

    # planned: back to the planner's dish and, apart from daily use, to the generated lines.
    box = (await put_meal(api, priya, box, set="planned")).json()
    assert meal(box, WEDNESDAY, "lunch")["recipe_id"] == ids["kobi-bhaji"]
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
        (None, WEDNESDAY, "lunch", NOW)
    }
    chain = [ids["kobi-bhaji"], chosen["recipe_id"], ids["palak-paneer"], None, None]
    assert [str(r.recipe_id) if r.recipe_id else None for r in rows] == chain
    assert [str(r.substitute_recipe_id) if r.substitute_recipe_id else None for r in rows] == [
        *chain[1:],
        ids["kobi-bhaji"],
    ]

    # On an approved week the redrawn lines go to M4 again.
    approved = await api.post(
        f"/v1/cycles/{box['id']}/approve", headers={**priya, **key(), "If-Match": '"6"'}
    )
    box = approved.json()
    after_approval = await put_meal(api, priya, box, set="tiffin")
    assert after_approval.json()["status"] == "approved"
    assert len(await events(db_session, "subscriptions.box_approved")) == 2

    # After the cut-off the week is fixed.
    await db_session.commit()
    with time_machine.travel(CUTOFF, tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        late = await put_meal(api, priya, after_approval.json(), set="planned")
    assert (late.status_code, late.json()["code"]) == (409, "subscriptions.cutoff_passed")


@time_machine.travel(NOW, tick=False)
async def test_a_swap_keeps_what_the_customer_added_and_daily_use(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    box = (await api.get("/v1/cycles/current", headers=priya)).json()
    by_slug = {line["slug"]: line for line in box["lines"]}
    cabbage, carrot = by_slug["cabbage"]["product_id"], by_slug["carrot"]["product_id"]
    offered = (await api.get(f"/v1/products/{cabbage}/substitutes", headers=priya)).json()
    substitute = offered["items"][0]
    # Carrot (only in gajar-matar) becomes the customer's own line of 3 steps; cabbage (only
    # in kobi-bhaji) is swapped for a substitute.
    edited = await api.patch(
        f"/v1/cycles/{box['id']}/lines",
        json={
            "ops": [
                {"op": "remove", "product_id": carrot},
                {"op": "add", "product_id": carrot, "qty": 3},
                {
                    "op": "substitute",
                    "product_id": cabbage,
                    "substitute_id": substitute["product_id"],
                },
            ]
        },
        headers={**priya, "If-Match": '"1"'},
    )
    start = edited.json()
    assert qty(start)["carrot"] == (3, "added_by_you")
    assert qty(start)[substitute["slug"]] == (1, "swapped_by_you")

    box = start
    for slot in ("lunch", "dinner"):  # kobi-bhaji lunches, gajar-matar dinners: all tiffins
        for on in (WEDNESDAY, SATURDAY):
            answer = await api.put(
                f"/v1/cycles/{box['id']}/meals",
                json={"date": on, "slot": slot, "set": "tiffin"},
                headers={**priya, "If-Match": f'"{box["version"]}"'},
            )
            assert answer.status_code == 200, answer.text
            box = answer.json()
    after = qty(box)
    assert after["carrot"] == (3, "added_by_you")
    assert substitute["slug"] not in after  # the substitute counts as the cabbage it replaced
    assert {s: q for s, q in after.items() if q[1] == "daily_use"} == {
        s: q for s, q in qty(start).items() if q[1] == "daily_use"
    }
    # Four meal rows; the product rows are the line edit's own.
    actions = (await db_session.execute(select(EditFeedback.action))).scalars().all()
    assert sorted(actions) == ["added", *["meal_tiffin"] * 4, "removed", "swapped"]


@time_machine.travel(NOW, tick=False)
async def test_the_budget_lock_refuses_only_a_swap_that_raises_the_total(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    ids = await recipe_ids(db_session)
    box = (await api.get("/v1/cycles/current", headers=priya)).json()
    locked = await api.put(
        "/v1/budget-lock",
        json={"month": "2026-10", "budget_paise": 50_000},
        headers={**priya, **key()},
    )
    assert locked.status_code == 200 and box["total_paise"] > 50_000  # already over

    # A swap that does not raise the total, or lowers it, goes through even over the lock.
    same = await put_meal(api, priya, box, set="tiffin")
    assert same.status_code == 200 and same.json()["total_paise"] == box["total_paise"]
    lower = await put_meal(api, priya, same.json(), date=SATURDAY, set="tiffin")  # no cabbage
    assert lower.status_code == 200 and lower.json()["total_paise"] < box["total_paise"]
    box = lower.json()
    # Raising it does not.
    for refused in (
        await put_meal(api, priya, box, date=SATURDAY, set="planned"),
        await put_meal(api, priya, box, set="recipe", recipe_id=ids["palak-paneer"]),
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
    restored = await put_meal(api, priya, box, date=SATURDAY, set="planned")
    assert restored.status_code == 200 and restored.json()["total_paise"] > box["total_paise"]
    assert meal(restored.json(), SATURDAY, "lunch")["recipe_id"] == ids["kobi-bhaji"]
