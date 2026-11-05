"""`just sim`: simulated households in the local database, and their ground truth as Parquet.

    just sim                                  # seed 7: 8 weeks from Mon 5 Oct 2026, CP-SAT plans
    just sim --seed 11 --weeks 4 --planner rules

Needs `just seed` first (the hub, societies, catalog and prices). Users and households are
written through M1's onboarding service with `data_source='synthetic'` (ADR-0008); running it
again keeps them as they are. The weeks (plans, edits, skips, consumption) go to
data/sim_truth/; with `--write-db` they also go through M3, M4 and M5, a week at a time
(through_modules.py), each week's add-ons as add-on lines on the approved box (uncommitted.py,
the eval harness's derivation; `--no-uncommitted` leaves them out).

Only for the local Docker database. A shared database (staging on Neon) is refused unless both
`--allow-remote` is given and GB_ALLOW_REMOTE_SEED names its host: `scripts/staging-demo.sh`
does that for staging's demo data (handbook 12).
"""

import argparse
import asyncio
import os
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.households import create_household, household_of
from gharbasket.modules.identity.schemas import HouseholdIn
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.platform.db import (
    RemoteDatabaseRefused,
    create_engine,
    is_local_database,
    is_pgbouncer,
    synthetic_data_target,
)
from gharbasket.platform.settings import DatabaseSettings
from gharbasket.tools.seed import SEED_HUB_ID
from gharbasket_sim import config as cohorts
from gharbasket_sim import through_modules as through
from gharbasket_sim import truth
from gharbasket_sim import uncommitted as demand
from gharbasket_sim.catalog import Catalog, load_catalog
from gharbasket_sim.config import Shocks
from gharbasket_sim.households import SimHousehold, sample
from gharbasket_sim.pantry import price_of, weekly_prices
from gharbasket_sim.weeks import PLANNERS, Rows, Simulation, cached

REPO_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_SEED = 7
DEFAULT_START = date(2026, 10, 5)  # a Monday; the weeks cover Navratri in the seed calendar


@dataclass(frozen=True)
class Run:
    seed: int
    weeks: int
    first_monday: date
    planner: str
    households: list[SimHousehold]
    created: dict[str, tuple[uuid.UUID, bool]]  # key → (household id, created now)
    catalog: Catalog
    rows: Rows
    prices: list[dict[str, Any]]


class RealUserOnSimulatorPhone(LookupError):
    """A simulator phone (+91 98001 …) belongs to a real account on a shared database."""


async def populate(
    session: AsyncSession, households: list[SimHousehold], *, adopt_real: bool = True
) -> dict[str, tuple[uuid.UUID, bool]]:
    """A user and a household for each, through M1's services; existing ones are kept.

    Locally a real account on a simulator phone (signed in by hand) becomes the synthetic one, as
    the seed does for its phones. On a shared database (`adopt_real=False`) that account may be a
    stranger who owns the number, so the run is refused instead, before anything is committed."""
    done: dict[str, tuple[uuid.UUID, bool]] = {}
    for h in households:
        user, created = await find_or_create_user(session, h.phone)
        if not created and user.data_source != "synthetic" and not adopt_real:
            raise RealUserOnSimulatorPhone(
                f"{h.phone} is a real account on a shared database: the simulator leaves it alone "
                "(use a lower --adoption, or move the account first)"
            )
        user.name = f"Sim {h.key}"
        user.locale = h.locale
        user.data_source = "synthetic"
        await session.flush()
        existing = await household_of(session, user.id)
        if existing is not None:
            done[h.key] = (existing.id, False)
            continue
        body = HouseholdIn.model_validate(h.body)
        household = await create_household(session, user.id, body, data_source="synthetic")
        done[h.key] = (household.id, True)
    return done


async def simulate_world(
    session: AsyncSession,
    *,
    seed: int,
    weeks: int,
    start: date,
    planner: str,
    adoption: float | None = None,
    shocks: Shocks | None = None,
    write_db: bool = False,
    progress: Callable[[str], None] | None = None,
    adopt_real: bool = True,
    pause_after: bool = False,
    place_uncommitted: bool = True,
) -> Run:
    """`pause_after` (staging) pauses the households' subscriptions once their weeks are written,
    so staging's own weekly job leaves them alone; the next `--write-db` run resumes them.
    `place_uncommitted` (`--write-db`) puts each week's add-ons (`uncommitted.week_demand`, the
    eval harness's derivation) on the approved box through M4, before the cut-off."""
    first_monday = start - timedelta(days=start.weekday())
    last_day = first_monday + timedelta(weeks=weeks, days=-1)
    catalog = await load_catalog(session, SEED_HUB_ID, first_monday, last_day)
    if not catalog.recipes:
        raise LookupError("the catalog is empty: run `just seed` first")
    households = sample(seed, cohorts.load(), catalog, adoption)
    created = await populate(session, households, adopt_real=adopt_real)
    plan = cached(PLANNERS[planner])
    by_name = {c.name: c for c in cohorts.load().cohorts}
    rows = Rows([], [], [])
    ids: dict[str, uuid.UUID] = {}
    slugs: dict[uuid.UUID, str] = {}
    settings = through.app_settings(planner)
    plan_catalog = None
    if write_db:
        await through.subscribe_all(session, households, created)
        ids = await catalog_api.product_ids_by_slug(session, sorted(catalog.products))
        slugs = {pid: slug for slug, pid in ids.items()}
        plan_catalog = await catalog_api.plan_catalog(session, SEED_HUB_ID)
    festival_days = demand.festival_days(catalog)
    async with through.ml_client() as http:
        for n, h in enumerate(households, 1):
            c = by_name[h.cohort]
            sim = Simulation(
                seed,
                h,
                catalog,
                first_monday,
                plan,
                shocks,
                price_sensitivity=c.price_sensitivity,
                feedback_rate=c.feedback_rate,
            )
            one = sim.rows
            if plan_catalog is None:
                for w in range(weeks):
                    sim.week(w)
            else:
                household_id = created[h.key][0]
                user_id = await identity_api.owner_of_household(session, household_id)
                assert user_id is not None
                deliveries = await through.delivery_dates(
                    session, uuid.UUID(h.society_id), first_monday, weeks
                )
                # A week at a time: M3 drafts it once the week before has been replayed.
                for w, delivery in enumerate(deliveries):
                    planned, drafted, cycle = await through.generate_week(
                        session,
                        http,
                        settings,
                        household_id=household_id,
                        delivery=delivery,
                        plan_catalog=plan_catalog,
                        slugs=slugs,
                    )
                    sim.week(w, planned, drafted)
                    addons = None
                    if place_uncommitted:
                        monday = first_monday + timedelta(weeks=w)
                        addons, _ = demand.week_demand(  # tiffins: no menus yet (M2 §3.4)
                            seed,
                            h,
                            one,
                            catalog,
                            w,
                            weekly_prices(catalog, shocks, w),
                            festival_days.get(monday, 0.0),
                        )
                    rng = demand.db_stream(seed, h.key, w)
                    outcome = await through.replay_week(
                        session,
                        settings,
                        rng,
                        h,
                        user_id=user_id,
                        cycle=cycle,
                        delivery=delivery,
                        week=w,
                        rows=one,
                        ids=ids,
                        slugs=slugs,
                        addons=addons,
                    )
                    row = through.outcome_row(h, w, outcome)
                    row["addons_derived"] = len(addons or [])
                    one.outcomes.append(row)
                await session.commit()
                if progress is not None:
                    progress(f"  {n}/{len(households)} {h.key}: {weeks} weeks through the modules")
            for name in (
                "weeks",
                "meals",
                "consumption",
                "boxes",
                "box_lines",
                "edits",
                "pantry",
                "outcomes",
            ):
                getattr(rows, name).extend(getattr(one, name))
    if write_db and pause_after:
        await through.pause_all(session, households, created)
    prices = [
        {"week": w, "product": slug, "price_paise": price, "multiplier": factor[slug]}
        for w in range(weeks)
        for factor in [weekly_prices(catalog, shocks, w)]
        for slug, p in sorted(catalog.products.items())
        if (price := price_of(p, factor)) is not None
    ]
    return Run(seed, weeks, first_monday, planner, households, created, catalog, rows, prices)


def tables(run: Run) -> dict[str, list[dict[str, Any]]]:
    hh = [
        {
            "key": h.key,
            "household_id": str(run.created[h.key][0]) if h.key in run.created else None,
            "society_id": h.society_id,
            "cohort": h.cohort,
            "size": h.rules["size"],
            "adult_equivalents": h.adult_equivalents,
            "diet": h.rules["diet"],
            "jain": h.rules["jain"],
            "diabetic": h.rules["diabetic"],
            "cuisines": h.rules["cuisines"],
            "fasts": list(h.fasts),
            "weekly_budget_paise": h.rules["weekly_budget_paise"],
            "accept_bias": h.accept_bias,
            "skip_week": h.skip_week,
        }
        for h in run.households
    ]
    units = {slug: p.unit for slug, p in run.catalog.products.items()}
    return {
        "households": hh,
        "daily_use": [
            {"household": h.key, "product": p, "unit": units[p], "weekly_qty": round(q, 2)}
            for h in run.households
            for p, q in h.daily_use.items()
        ],
        "taste": [
            {"household": h.key, "recipe": r, "taste": round(t, 4)}
            for h in run.households
            for r, t in sorted(h.taste.items())
        ],
        "weeks": run.rows.weeks,
        "meals": run.rows.meals,
        "consumption": run.rows.consumption,
        "boxes": run.rows.boxes,
        "box_lines": run.rows.box_lines,
        "edits": run.rows.edits,
        "pantry": run.rows.pantry,
        "prices": run.prices,
        "outcomes": run.rows.outcomes,
    }


def _counts(values: Iterable[str]) -> str:
    return ", ".join(f"{n} {v}" for v, n in sorted(Counter(values).items()))


def summary(run: Run) -> list[str]:
    lines = _summary(run)
    return lines


def _summary(run: Run) -> list[str]:
    new = sum(created for _, created in run.created.values())
    actions = Counter(m["action"] for m in run.rows.meals)
    planned = actions["accept"] + actions["edit"]
    statuses = Counter(w["plan_status"] for w in run.rows.weeks)
    skipped = sum(not w["ordered"] for w in run.rows.weeks)
    relaxed = sum(bool(w["relaxed"]) for w in run.rows.weeks)
    lines = [
        f"{len(run.households)} synthetic households ({new} new) across "
        f"{len({h.society_id for h in run.households})} societies",
        f"{run.weeks} weeks from {run.first_monday}, planner {run.planner}: "
        + ", ".join(f"{n} {s}" for s, n in sorted(statuses.items()))
        + f" ({relaxed} with a comfort rule relaxed)",
        f"meals: {actions['accept'] / max(planned, 1):.0%} of planned dishes accepted, "
        f"{actions['edit']} edited, {actions['tiffin']} tiffins; "
        f"{skipped} of {len(run.rows.weeks)} weekly boxes skipped",
        f"boxes: {len(run.rows.edits) / max(len(run.rows.boxes), 1):.1f} edits a box "
        f"({_counts(e['reason'] for e in run.rows.edits)}); "
        f"pantry taps: {_counts(p['signal'] for p in run.rows.pantry if p['signal'])}; "
        f"{sum(1 for p in run.prices if p['multiplier'] > 1)} product-weeks with a price shock",
    ]
    if run.rows.outcomes:
        lines.append(
            "through the modules: "
            f"{_counts(o['cycle_status'] for o in run.rows.outcomes)} cycles; "
            f"orders {_counts(o['order_status'] for o in run.rows.outcomes if o['order_status'])}; "
            f"{sum(o['ops'] for o in run.rows.outcomes)} line ops, "
            f"{sum(o['feedback'] for o in run.rows.outcomes)} pantry taps recorded; refused: "
            f"{_counts(code for o in run.rows.outcomes for code in o['refused']) or 'none'}"
        )
        placed = [o for o in run.rows.outcomes if o["addon_lines"]]
        sold = [o for o in placed if o["order_status"] == "delivered"]
        # Weeks the harness keeps add-ons for (funded) that the database did not deliver: a
        # refused approval, the wallet cap at the top-up, or M4 refusing the add-ons.
        gap = [
            o
            for o in run.rows.outcomes
            if o.get("addons_derived")
            and o["order_status"] != "delivered"
            and demand.funded(run.seed, o["household"], o["week"])
        ]
        lines.append(
            f"add-ons: {sum(o['addon_lines'] for o in placed)} lines "
            f"({sum(o['addon_steps'] for o in placed)} steps) on {len(placed)} boxes, "
            f"{sum(o['addon_lines'] for o in sold)} lines delivered, {len(gap)} funded weeks "
            "lost theirs; tiffin orders: 0 (no tiffin menus yet)"
        )
    return lines


async def run(database_url: str, args: argparse.Namespace) -> Run:
    engine = create_engine(
        database_url, pooled=False, cache_statements=not is_pgbouncer(database_url)
    )
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await simulate_world(
                session,
                seed=args.seed,
                weeks=args.weeks,
                start=args.start,
                planner=args.planner,
                adoption=args.adoption,
                shocks=None if args.no_shocks else cohorts.load_shocks(),
                write_db=args.write_db,
                progress=(lambda line: print(line, flush=True)) if args.write_db else None,
                adopt_real=is_local_database(database_url),
                pause_after=args.pause_after,
                place_uncommitted=not args.no_uncommitted,
            )
            await session.commit()
            return result
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate synthetic households (ADR-0008).")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--weeks", type=int, default=8)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--planner", choices=sorted(PLANNERS), default="cpsat")
    parser.add_argument("--adoption", type=float, help="share of flats (default: cohorts.json)")
    parser.add_argument("--out", type=Path, help="default: data/sim_truth/seed-<seed>")
    parser.add_argument(
        "--no-shocks", action="store_true", help="flat prices (no episodes from shocks.json)"
    )
    parser.add_argument(
        "--write-db",
        action="store_true",
        help="send the weeks through M3, M4 and M5 (cycles, edits, orders, wallet, feedback)",
    )
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="a shared database (staging); also needs GB_ALLOW_REMOTE_SEED=<its host>",
    )
    parser.add_argument(
        "--pause-after",
        action="store_true",
        help="with --write-db: pause the households' subscriptions at the end (staging)",
    )
    parser.add_argument(
        "--no-uncommitted",
        action="store_true",
        help="with --write-db: boxes only, no add-on lines (default: the week's add-ons go on the "
        "approved box through M4, the eval harness's derivation; ADR-0031)",
    )
    args = parser.parse_args(argv)
    if args.pause_after and not args.write_db:
        parser.error("--pause-after needs --write-db")

    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    try:
        host = synthetic_data_target(url, allow_remote=args.allow_remote, environ=os.environ)
    except RemoteDatabaseRefused as refused:
        parser.error(f"the simulator writes only to the local Docker database: {refused}")
    if not is_local_database(url):
        print(f"simulating into the shared database at {host} (data_source synthetic)")

    try:
        result = asyncio.run(run(url, args))
    except RealUserOnSimulatorPhone as refused:  # nothing was committed
        parser.error(str(refused))
    out = args.out or REPO_ROOT / "data" / "sim_truth" / f"seed-{args.seed}"
    manifest = {
        "seed": args.seed,
        "weeks": args.weeks,
        "first_monday": result.first_monday,
        "planner": args.planner,
        "shocks": not args.no_shocks,
        "write_db": args.write_db,
        "uncommitted": args.write_db and not args.no_uncommitted,
        "adoption": args.adoption,
        "data_source": "synthetic",
    }
    truth.write(out, tables(result), manifest)
    for line in summary(result):
        print(line)
    print(f"ground truth: {out}")
    return 0
