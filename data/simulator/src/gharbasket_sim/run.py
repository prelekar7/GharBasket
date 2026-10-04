"""`just sim`: simulated households in the local database, and their ground truth as Parquet.

    just sim                                  # seed 7: 8 weeks from Mon 5 Oct 2026, CP-SAT plans
    just sim --seed 11 --weeks 4 --planner rules

Needs `just seed` first (the hub, societies, catalog and prices). Users and households are
written through M1's onboarding service with `data_source='synthetic'` (ADR-0008); running it
again keeps them as they are. The weeks (plans, edits, skips, consumption) go only to
data/sim_truth/ for now: they reach the database through M3/M4 once those modules exist.

Only for the local Docker database: shared databases (staging) are always refused.
"""

import argparse
import asyncio
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.households import create_household, household_of
from gharbasket.modules.identity.schemas import HouseholdIn
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.settings import DatabaseSettings
from gharbasket.tools.seed import SEED_HUB_ID
from gharbasket_sim import config as cohorts
from gharbasket_sim import truth
from gharbasket_sim.catalog import Catalog, load_catalog
from gharbasket_sim.config import Shocks
from gharbasket_sim.households import SimHousehold, sample
from gharbasket_sim.pantry import price_of, weekly_prices
from gharbasket_sim.weeks import PLANNERS, Rows, cached, simulate

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


async def populate(
    session: AsyncSession, households: list[SimHousehold]
) -> dict[str, tuple[uuid.UUID, bool]]:
    """A user and a household for each, through M1's services; existing ones are kept."""
    done: dict[str, tuple[uuid.UUID, bool]] = {}
    for h in households:
        user, _ = await find_or_create_user(session, h.phone)
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
) -> Run:
    first_monday = start - timedelta(days=start.weekday())
    last_day = first_monday + timedelta(weeks=weeks, days=-1)
    catalog = await load_catalog(session, SEED_HUB_ID, first_monday, last_day)
    if not catalog.recipes:
        raise LookupError("the catalog is empty: run `just seed` first")
    households = sample(seed, cohorts.load(), catalog, adoption)
    created = await populate(session, households)
    plan = cached(PLANNERS[planner])
    by_name = {c.name: c for c in cohorts.load().cohorts}
    rows = Rows([], [], [])
    for h in households:
        c = by_name[h.cohort]
        one = simulate(
            seed,
            h,
            catalog,
            first_monday,
            weeks,
            plan,
            shocks,
            price_sensitivity=c.price_sensitivity,
            feedback_rate=c.feedback_rate,
        )
        for name in ("weeks", "meals", "consumption", "boxes", "box_lines", "edits", "pantry"):
            getattr(rows, name).extend(getattr(one, name))
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
    }


def _counts(values: Iterable[str]) -> str:
    return ", ".join(f"{n} {v}" for v, n in sorted(Counter(values).items()))


def summary(run: Run) -> list[str]:
    new = sum(created for _, created in run.created.values())
    actions = Counter(m["action"] for m in run.rows.meals)
    planned = actions["accept"] + actions["edit"]
    statuses = Counter(w["plan_status"] for w in run.rows.weeks)
    skipped = sum(not w["ordered"] for w in run.rows.weeks)
    relaxed = sum(bool(w["relaxed"]) for w in run.rows.weeks)
    return [
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


async def run(database_url: str, args: argparse.Namespace) -> Run:
    engine = create_engine(database_url, pooled=False)
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
    args = parser.parse_args(argv)

    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    if not is_local_database(url):
        parser.error("the simulator writes only to the local Docker database")

    result = asyncio.run(run(url, args))
    out = args.out or REPO_ROOT / "data" / "sim_truth" / f"seed-{args.seed}"
    manifest = {
        "seed": args.seed,
        "weeks": args.weeks,
        "first_monday": result.first_monday,
        "planner": args.planner,
        "shocks": not args.no_shocks,
        "adoption": args.adoption,
        "data_source": "synthetic",
    }
    truth.write(out, tables(result), manifest)
    for line in summary(result):
        print(line)
    print(f"ground truth: {out}")
    return 0
