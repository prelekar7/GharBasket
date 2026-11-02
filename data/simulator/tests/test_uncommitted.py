"""The uncommitted demand beside the box (ADR-0031): one derivation for the eval harness and
`--write-db`, and the add-on lines `--write-db` places through M4."""

import uuid
from collections import defaultdict
from datetime import date, timedelta

import pytest
from gharbasket_sim import run as sim
from gharbasket_sim import uncommitted
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample
from gharbasket_sim.pantry import weekly_prices
from gharbasket_sim.run import simulate_world
from gharbasket_sim.weeks import Rows
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.orders.api import MAX_ADDON_LINES, MAX_ADDON_QTY, SOLD
from gharbasket.modules.orders.models import Order, OrderLine
from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

from .conftest import json_catalog

START = date(2026, 10, 5)


def test_a_price_shock_lowers_the_add_on_take_and_a_festival_raises_it() -> None:
    usual = uncommitted.take(1.0, 0)
    assert usual == uncommitted.ADDON_TAKE == 0.5 and uncommitted.TIFFIN_TAKE == 0.7
    assert uncommitted.take(1.8, 0) == pytest.approx(usual / 1.8)
    assert uncommitted.take(1.0, 2) == pytest.approx(usual * 1.2)
    assert uncommitted.take(0.1, 7) == 1.0


def test_funding_is_the_write_db_streams_first_draw() -> None:
    draws = [uncommitted.funded(7, "h1", w) for w in range(200)]
    assert draws == [uncommitted.funded(7, "h1", w) for w in range(200)]  # seeded
    assert 0 < draws.count(False) < 30  # about UNFUNDED of the weeks
    first = uncommitted.db_stream(7, "h1", 3).random()
    assert uncommitted.funded(7, "h1", 3) == (first >= uncommitted.UNFUNDED)


def test_festival_days_count_each_weeks_distinct_days(catalog: Catalog) -> None:
    days = uncommitted.festival_days(catalog)
    assert all(monday.weekday() == 0 and n >= 1 for monday, n in days.items())
    assert sum(days.values()) == len({f.day for f in catalog.festivals})


def rows_of(key: str, *, ordered: bool, on_box: list[str], short: dict[str, float]) -> Rows:
    rows = Rows([], [], [])
    rows.boxes.append({"household": key, "week": 0, "ordered": ordered})
    rows.box_lines += [{"household": key, "week": 0, "product": p} for p in on_box]
    rows.pantry += [
        {"household": key, "week": 0, "product": p, "bought_elsewhere": q}
        for p, q in sorted(short.items())
    ]
    rows.meals += [
        {"household": key, "week": 0, "date": START + timedelta(days=6), "slot": s, "action": a}
        for s, a in (("lunch", "tiffin"), ("dinner", "tiffin"), ("breakfast", "accept"))
    ]
    return rows


def test_add_ons_ride_only_on_an_ordered_box_and_never_name_a_box_line(catalog: Catalog) -> None:
    h = sample(7, sim.cohorts.load(), catalog, 0.02)[0]
    flat = {slug: 1.0 for slug in catalog.products}
    priced = sorted(s for s, p in catalog.products.items() if p.price_paise is not None)
    short = {slug: 10_000.0 for slug in priced[:30]}  # a shortfall of every one of them
    take_all = 7.0 / uncommitted.FESTIVAL_LIFT  # festival days that lift the take to 1

    addons, tiffins = uncommitted.week_demand(
        7,
        h,
        rows_of(h.key, ordered=True, on_box=priced[:2], short=short),
        catalog,
        0,
        flat,
        take_all,
    )
    products = [a.product for a in addons]
    assert not set(products) & set(priced[:2])  # M4 refuses a box line as an add-on
    assert products == sorted(products) and len(products) == MAX_ADDON_LINES
    assert all(1 <= a.qty <= MAX_ADDON_QTY for a in addons)
    assert {t.slot for t in tiffins} <= {"lunch", "dinner"}
    assert all(t.portions == len(h.body["members"]) for t in tiffins)

    skipped, again = uncommitted.week_demand(
        7, h, rows_of(h.key, ordered=False, on_box=[], short=short), catalog, 0, flat, take_all
    )
    assert skipped == [] and again == tiffins  # no box, no add-ons; the draws are unchanged


async def seeded(session: AsyncSession) -> None:
    await seed_societies(session)
    await seed_households(session)
    await seed_catalog(session)
    await seed_delivery(session)


async def addon_lines(session: AsyncSession) -> dict[tuple[uuid.UUID, date, uuid.UUID], int]:
    found = await session.execute(
        select(Order.household_id, Order.delivery_date, OrderLine.product_id, OrderLine.qty)
        .join(Order, Order.id == OrderLine.order_id)
        .where(OrderLine.kind == "addon", Order.data_source == "synthetic")
    )
    return {(hh, day, product): qty for hh, day, product, qty in found.all()}


@pytest.mark.anyio
@pytest.mark.services
async def test_write_db_places_the_eval_derivations_add_ons_on_the_boxes(
    db_session: AsyncSession,
) -> None:
    await seeded(db_session)
    run = await simulate_world(
        db_session, seed=7, weeks=2, start=START, planner="rules", adoption=0.02, write_db=True
    )
    catalog = json_catalog()
    ids = await catalog_api.product_ids_by_slug(db_session, sorted(catalog.products))
    festival_days = uncommitted.festival_days(run.catalog)
    orders = {
        str(o.id): o
        for o in (await db_session.execute(select(Order).where(Order.kind == "box"))).scalars()
    }

    # The same derivation over the run's own rows (M3's plans and boxes) is what M4 holds.
    expected: dict[tuple[uuid.UUID, date, uuid.UUID], int] = {}
    sold: dict[str, int] = defaultdict(int)
    for h in run.households:
        for w in range(run.weeks):
            monday = run.first_monday + timedelta(weeks=w)
            addons, _ = uncommitted.week_demand(
                7,
                h,
                run.rows,
                run.catalog,
                w,
                weekly_prices(run.catalog, None, w),  # simulate_world's default: no shocks
                festival_days.get(monday, 0.0),
            )
            outcome = next(
                o for o in run.rows.outcomes if o["household"] == h.key and o["week"] == w
            )
            order = orders.get(outcome["order_id"] or "")
            if order is None:
                assert outcome["addon_lines"] == 0
                continue
            for a in addons:
                expected[order.household_id, order.delivery_date, ids[a.product]] = a.qty
            assert outcome["addon_lines"] == len(addons)
            assert outcome["addon_steps"] == sum(a.qty for a in addons)
            if order.status in SOLD:
                sold["weeks"] += 1
                # The harness keeps a week's add-ons exactly when the simulator funded its box.
                assert uncommitted.funded(7, h.key, w)
            elif addons:
                assert order.status == "cancelled" and not uncommitted.funded(7, h.key, w)
    assert expected and await addon_lines(db_session) == expected
    assert sold["weeks"] > 0
    # Integer paise: the add-ons are priced into the order and the cut-off debited the total.
    for order in orders.values():
        lines = (
            await db_session.execute(select(OrderLine).where(OrderLine.order_id == order.id))
        ).scalars()
        assert order.subtotal_paise == sum(line.qty * line.unit_price_paise for line in lines)
    assert any("add-ons:" in line for line in sim.summary(run))


@pytest.mark.anyio
@pytest.mark.services
async def test_no_uncommitted_places_boxes_only_and_a_replay_sets_the_same_lines(
    db_session: AsyncSession,
) -> None:
    await seeded(db_session)
    await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=START,
        planner="rules",
        adoption=0.02,
        write_db=True,
        place_uncommitted=False,
    )
    assert await addon_lines(db_session) == {}
    assert (await db_session.execute(select(Order))).scalars().first() is not None

    run = await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=START + timedelta(weeks=1),
        planner="rules",
        adoption=0.02,
        write_db=True,
    )
    placed = await addon_lines(db_session)
    assert placed
    # A replay of the placement on the same approved box sets the same lines (quantities are set,
    # not added), as a retried POST /v1/orders/{id}/addons would.
    from gharbasket_sim import through_modules as through

    household_id, day, _ = sorted(placed)[0]
    order = (
        await db_session.execute(
            select(Order).where(
                Order.household_id == household_id,
                Order.delivery_date == day,
                Order.kind == "box",
            )
        )
    ).scalar_one()
    order.status = "approved"  # back before its cut-off, for the replay only
    ids = await catalog_api.product_ids_by_slug(db_session, sorted(run.catalog.products))
    slugs = {pid: slug for slug, pid in ids.items()}
    mine = {
        product: qty
        for (hh, day, product), qty in placed.items()
        if hh == order.household_id and day == order.delivery_date
    }
    addons = [uncommitted.AddOn(1, slugs[p], q) for p, q in mine.items()]
    outcome = through.Outcome()
    await through.place_addons(db_session, order, addons, ids, order.cutoff_at, outcome)
    assert await addon_lines(db_session) == placed
    assert mine and outcome.addon_lines == len(mine)
