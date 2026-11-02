"""The uncommitted demand a simulated household orders beside its box (M10 §3.6, ADR-0031): add-on
lines and tiffins. This is the one derivation behind both the eval harness's RQ3
(`gharbasket_training.eval.forecast_ab`) and `--write-db` (through_modules.py places the add-ons
through M4), so the two share one derivation for a seed.

- **Add-ons:** a product the household ran short of in a week (the pantry's `bought_elsewhere`,
  bought outside its box) becomes an add-on with probability `ADDON_TAKE`, times the week's price
  multiplier to the power `-PRICE_ELASTICITY` (shocks.json) and `1 + FESTIVAL_LIFT` per festival
  day of the week, capped at 1. The quantity is the shortfall in the product's steps (at least
  one, at most M4's `MAX_ADDON_QTY`). An add-on is a line on the week's box (add-on-only orders
  are refused, M4 §3.5), so it exists only in a week the household ordered; never for a product
  already on that box (M4 refuses it: the household edits the box line instead) or without a
  price at the hub; at most `MAX_ADDON_LINES` a box, in the pantry rows' order. It is delivered
  with the box. Whether the box is paid is the cut-off's business: `funded` is the simulator's
  own draw for it, which the harness applies (an unfunded box and its add-ons are cancelled).
  **The funding gap:** the database path can still lose a funded week's add-ons where the
  harness keeps them: when M3 refuses the approval (no order to ride on), when the top-up hits
  the wallet's monthly cap (`billing.wallet_limit`, the box then fails at the cut-off), or when
  M4 refuses the add-ons themselves. `--write-db`'s summary counts those weeks.
- **Tiffins:** a slot the plan leaves to a no-cook day (meal action `tiffin`) is ordered as a
  tiffin with probability `TIFFIN_TAKE`, one portion per member. `--write-db` does not place
  them yet: no tiffin menus or products exist in any environment (M2 §3.4).

Every draw comes from one stream per seed, household and week, in the week's pantry rows' order
and then its meals' order.
"""

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta

from gharbasket.modules.orders.api import MAX_ADDON_LINES, MAX_ADDON_QTY
from gharbasket_sim import pantry as pantry_sim
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import SimHousehold
from gharbasket_sim.weeks import Rows

ADDON_TAKE = 0.5  # share of shortfalls ordered as an add-on rather than at the corner shop
PRICE_ELASTICITY = 1.0
FESTIVAL_LIFT = 0.1  # per festival day of the week
TIFFIN_TAKE = 0.7  # share of no-cook slots ordered as a tiffin
UNFUNDED = 0.05  # chance a household's wallet is left short at the cut-off in a week


@dataclass(frozen=True)
class AddOn:
    week: int
    product: str  # slug
    qty: int  # steps


@dataclass(frozen=True)
class Tiffin:
    week: int
    day: date
    slot: str  # lunch, dinner …
    portions: int


def stream(seed: int, household: str, week: int) -> random.Random:
    return random.Random(f"gharbasket-eval-forecast:{seed}:{household}:{week}")


def funded(seed: int, household: str, week: int) -> bool:
    """Whether the household's wallet is topped up for the week's box at the cut-off: the first
    draw of `--write-db`'s per-week stream (run.py, through_modules.replay_week)."""
    return db_stream(seed, household, week).random() >= UNFUNDED


def db_stream(seed: int, household: str, week: int) -> random.Random:
    """`--write-db`'s stream for one household-week (the cut-off's funding draw comes first)."""
    return random.Random(f"gharbasket-sim:{seed}:{household}:{week}:db")


def take(multiplier: float, festival_days: float) -> float:
    """The chance that a shortfall becomes an add-on in a week."""
    lift = 1 + FESTIVAL_LIFT * festival_days
    return min(1.0, ADDON_TAKE * multiplier**-PRICE_ELASTICITY * lift)


def festival_days(catalog: Catalog) -> dict[date, float]:
    """Festival days in each week, by its Monday."""
    days: dict[date, set[date]] = {}
    for f in catalog.festivals:
        days.setdefault(f.day - timedelta(days=f.day.weekday()), set()).add(f.day)
    return {monday: float(len(found)) for monday, found in days.items()}


def week_demand(
    seed: int,
    h: SimHousehold,
    rows: Rows,
    catalog: Catalog,
    week: int,
    factor: dict[str, float],
    festival_days: float,
) -> tuple[list[AddOn], list[Tiffin]]:
    """The household's add-ons and tiffins of `week`, from its simulated rows (the week's pantry,
    box and meals must be in `rows`): add-ons only on a box it ordered; funding is not judged
    here (`funded`)."""
    rng = stream(seed, h.key, week)
    mine = lambda r: r["household"] == h.key and r["week"] == week  # noqa: E731
    ordered = any(b["ordered"] for b in rows.boxes if mine(b))
    on_box = {line["product"] for line in rows.box_lines if mine(line)}
    addons: list[AddOn] = []
    for p in (r for r in rows.pantry if mine(r)):
        slug, short = p["product"], p["bought_elsewhere"]
        chance = take(factor[slug], festival_days)
        if short <= 0 or rng.random() >= chance:
            continue
        product = catalog.products[slug]
        if (
            not ordered
            or slug in on_box
            or pantry_sim.price_of(product, factor) is None
            or len(addons) >= MAX_ADDON_LINES
        ):
            continue  # no box to ride on, or M4 refuses it
        steps = max(1, math.ceil(short / (product.step or 1)))
        addons.append(AddOn(week, slug, min(MAX_ADDON_QTY, steps)))
    tiffins = [
        Tiffin(week, m["date"], m["slot"], len(h.body["members"]))
        for m in rows.meals
        if mine(m) and m["action"] == "tiffin" and rng.random() < TIFFIN_TAKE
    ]
    return addons, tiffins
