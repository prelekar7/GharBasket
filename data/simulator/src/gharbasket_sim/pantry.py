"""The box and the pantry (simulator v1a, handbook 05 §5.11 items 3 and 5).

Each week a household gets a box drafted the way M3 drafts it (the planned dishes' ingredients
plus the family-size rule table, in steps at this week's prices), edits it from what it knows
(its pantry, its tastes, the prices) and, after the week, taps "ran out", "wasted" or "just
right" on some items. Every edit and tap is ground truth for the ranker (handbook 08 M-B) and
the pantry twin (M-A).

Pure and deterministic for a given `random.Random`.
"""

import random
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Any

from gharbasket.modules.subscriptions import rules
from gharbasket_sim.catalog import Catalog, Product
from gharbasket_sim.config import Shocks
from gharbasket_sim.households import SimHousehold

# A price `SPIKE` times the list price or more counts as a spike the household reacts to.
SPIKE = 1.3
# After a week a perishable left in the pantry is waste; staples carry over up to this many weeks.
CARRY_WEEKS = 3
STOCK_LOOKS = 3  # how many over-stocked items a household bothers to edit in a week
ADD_FRUIT = 0.2  # chance the household adds a fruit it likes that the box does not carry
TAP_WASTED_SHARE = 0.25  # a perishable with this share of the delivered quantity left is "wasted"


@dataclass(frozen=True)
class Line:
    product: str
    qty: int  # steps
    step: float  # base units a step
    unit_price_paise: int
    reason: str  # meal_plan | daily_use | in_season | added_by_you | swapped_by_you
    source: str  # ai | rule | customer
    replaces: str | None = None

    @property
    def base_units(self) -> float:
        return self.qty * self.step

    @property
    def total_paise(self) -> int:
        return self.qty * self.unit_price_paise


@dataclass
class Pantry:
    stock: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    age_weeks: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def weekly_prices(catalog: Catalog, shocks: Shocks | None, week: int) -> dict[str, float]:
    """Price multiplier per product this week (1.0 outside an episode)."""
    factor: dict[str, float] = defaultdict(lambda: 1.0)
    if shocks is None:
        return factor
    for shock in shocks.episodes:
        if shock.start_week <= week < shock.start_week + shock.weeks:
            factor[shock.product] = max(factor[shock.product], shock.multiplier)
    return factor


def price_of(product: Product, factor: dict[str, float]) -> int | None:
    if product.price_paise is None:
        return None
    return round(product.price_paise * factor[product.slug])


def steps(product: Product, base_units: float) -> int:
    return rules.steps_needed(base_units, 1, int(product.step)) if product.step else 0


def draft_box(
    h: SimHousehold,
    catalog: Catalog,
    planned: list[str],
    factor: dict[str, float],
    pantry: "Pantry | None" = None,
) -> list[Line]:
    """M3's draft: the planned dishes' ingredients (per serving x adult equivalents, optional
    ones included) rounded up to steps, the rule table for daily use, two fruits in season;
    staples the pantry still holds are not ordered again (M3's carry-over rule)."""
    need: dict[str, float] = defaultdict(float)
    for slug in planned:
        for i in catalog.ingredients.get(slug, ()):
            need[i.product] += i.qty * h.adult_equivalents
    stock = pantry.stock if pantry is not None else {}

    def spare(product: Product, base_units: float) -> float:
        return base_units - (0.0 if product.perishable else stock.get(product.slug, 0.0))

    def line(product: Product, base_units: float, reason: str, source: str) -> Line | None:
        price = price_of(product, factor)
        wanted = spare(product, base_units)
        if price is None or wanted <= 0:
            return None
        return Line(product.slug, steps(product, wanted), product.step, price, reason, source)

    lines: list[Line] = []
    for slug in sorted(need):
        product = catalog.products.get(slug)
        if product is not None and (made := line(product, need[slug], "meal_plan", "ai")):
            lines.append(made)
    for slug, base_units in rules.daily_use_plan(h.adult_equivalents).items():
        product = catalog.products.get(slug)
        if product is None or slug in need:
            continue
        if made := line(product, base_units, "daily_use", "rule"):
            lines.append(made)
    fruits = [
        (price_of(p, factor), slug)
        for slug in catalog.fruits()
        if (p := catalog.products[slug]).price_paise is not None
    ]
    grams = rules.FRUIT_PER_ADULT_WEEK_G * h.adult_equivalents / rules.FRUIT_KINDS
    for _, slug in sorted(fruits)[: rules.FRUIT_KINDS]:
        product = catalog.products[slug]
        if made := line(product, product.base_units(grams), "in_season", "rule"):
            lines.append(made)
    return lines


def _cheaper_substitute(
    catalog: Catalog, product: Product, factor: dict[str, float]
) -> Product | None:
    """A product of the same category at a lower price this week, carrying no tag the
    original avoids (the catalog's substitute rule, M2)."""
    best: tuple[int, Product] | None = None
    for other in catalog.products.values():
        if other.slug == product.slug or other.category != product.category:
            continue
        if (other.tags & {"root", "onion_garlic", "egg", "dairy"}) - product.tags:
            continue
        price = price_of(other, factor)
        if price is None or price >= (price_of(product, factor) or 0):
            continue
        if best is None or price < best[0]:
            best = (price, other)
    return None if best is None else best[1]


def edit_box(
    rng: random.Random,
    h: SimHousehold,
    catalog: Catalog,
    lines: list[Line],
    *,
    planned: list[str],
    cooked: list[str],
    pantry: Pantry,
    factor: dict[str, float],
    price_sensitivity: float,
) -> tuple[list[Line], list[dict[str, Any]]]:
    """What the household changes before approving, and why (the ranker's labels):
    - the lines follow the dishes it will cook (a meal swap redraws them, not a line edit);
    - a few over-stocked items go or shrink (`stock`);
    - a spiked item is swapped for a cheaper one or dropped (`price`);
    - sometimes a liked fruit is added (`taste`)."""
    edits: list[dict[str, Any]] = []
    by_product = {line.product: line for line in lines}
    need_planned: dict[str, float] = defaultdict(float)
    need_cooked: dict[str, float] = defaultdict(float)
    for slug in planned:
        for i in catalog.ingredients.get(slug, ()):
            need_planned[i.product] += i.qty * h.adult_equivalents
    for slug in cooked:
        for i in catalog.ingredients.get(slug, ()):
            need_cooked[i.product] += i.qty * h.adult_equivalents

    def record(action: str, product: str, reason: str, substitute: str | None = None) -> None:
        edits.append(
            {"product": product, "action": action, "reason": reason, "substitute": substitute}
        )

    # 1. The household's own dishes replace the rejected ones' ingredients: the app redraws
    #    the lines when a meal is swapped, so this is not a line edit (no label).
    for slug in sorted(set(need_planned) | set(need_cooked)):
        line = by_product.get(slug)
        product = catalog.products.get(slug)
        if product is None or line is None or line.reason != "meal_plan":
            continue
        wanted = need_cooked.get(slug, 0.0)
        if wanted <= 0:
            del by_product[slug]
        elif steps(product, wanted) != line.qty:
            by_product[slug] = replace(line, qty=steps(product, wanted))
    for slug in sorted(need_cooked):
        if slug in by_product or need_cooked[slug] <= 0:
            continue
        product = catalog.products.get(slug)
        price = price_of(product, factor) if product else None
        if product is None or price is None:
            continue
        by_product[slug] = Line(
            slug, steps(product, need_cooked[slug]), product.step, price, "meal_plan", "ai"
        )

    # 2. What the pantry still holds: the few most over-stocked items get a look.
    overstocked = []
    for slug, line in sorted(by_product.items()):
        product = catalog.products[slug]
        have = pantry.stock.get(slug, 0.0)
        if product.perishable or have <= 0:
            continue
        spare = steps(product, have) - 1  # a step in hand is safety stock
        if spare > 0:
            overstocked.append((spare / max(line.qty, 1), slug, spare, line))
    for _, slug, spare, line in sorted(overstocked, reverse=True)[:STOCK_LOOKS]:
        if spare >= line.qty and rng.random() < 0.7:
            del by_product[slug]
            record("removed", slug, "stock")
        elif rng.random() < 0.5:
            by_product[slug] = replace(line, qty=max(1, line.qty - spare))
            record("qty_down", slug, "stock")

    # 3. Price spikes: swap or drop, more readily for price-sensitive households.
    for slug, line in sorted(by_product.items()):
        if factor[slug] < SPIKE or rng.random() >= price_sensitivity:
            continue
        product = catalog.products[slug]
        other = _cheaper_substitute(catalog, product, factor)
        if other is not None and other.slug not in by_product:
            price = price_of(other, factor)
            assert price is not None
            del by_product[slug]
            by_product[other.slug] = Line(
                other.slug,
                steps(other, line.base_units),
                other.step,
                price,
                "swapped_by_you",
                "customer",
                replaces=slug,
            )
            record("swapped", slug, "price", other.slug)
        elif line.reason in ("meal_plan", "in_season") and line.qty > 1:
            by_product[slug] = replace(line, qty=max(1, line.qty // 2))
            record("qty_down", slug, "price")

    # 4. A fruit the household likes.
    if rng.random() < ADD_FRUIT:
        favourites = sorted(
            (h.daily_use.get(f, 0.0), f) for f in catalog.fruits() if f not in by_product
        )
        if favourites:
            _, slug = favourites[-1]
            product = catalog.products[slug]
            price = price_of(product, factor)
            if price is not None:
                by_product[slug] = Line(slug, 1, product.step, price, "added_by_you", "customer")
                record("added", slug, "taste")
    return [by_product[s] for s in sorted(by_product)], edits


def deliver(pantry: Pantry, lines: list[Line]) -> None:
    for line in lines:
        pantry.stock[line.product] += line.base_units
        pantry.age_weeks[line.product] = 0


def consume(
    rng: random.Random,
    pantry: Pantry,
    catalog: Catalog,
    used: dict[str, float],
    delivered: dict[str, float],
    *,
    feedback_rate: float,
) -> list[dict[str, Any]]:
    """Eat the week's consumption from the pantry; what runs short is bought elsewhere, what
    is left of perishables is waste, and the household taps a signal on some items."""
    rows: list[dict[str, Any]] = []
    for slug in sorted(set(used) | set(delivered) | set(pantry.stock)):
        product = catalog.products.get(slug)
        if product is None:
            continue
        start = pantry.stock.get(slug, 0.0)
        eaten = used.get(slug, 0.0)
        shortfall = max(0.0, eaten - start)
        end = max(0.0, start - eaten)
        wasted = 0.0
        if product.perishable:
            wasted, end = end, 0.0
        else:
            pantry.age_weeks[slug] += 1
            if pantry.age_weeks[slug] > CARRY_WEEKS:
                wasted, end = end, 0.0
        pantry.stock[slug] = end
        ran_out = shortfall > 0 and delivered.get(slug, 0.0) > 0
        was_wasted = delivered.get(slug, 0.0) > 0 and wasted >= TAP_WASTED_SHARE * delivered[slug]
        signal = None
        if delivered.get(slug, 0.0) > 0 and rng.random() < feedback_rate:
            signal = "ran_out" if ran_out else ("wasted" if was_wasted else "just_right")
        if start == 0 and eaten == 0 and delivered.get(slug, 0.0) == 0:
            continue
        rows.append(
            {
                "product": slug,
                "unit": product.unit,
                "start_stock": round(start, 1),
                "delivered": round(delivered.get(slug, 0.0), 1),
                "used": round(eaten, 1),
                "bought_elsewhere": round(shortfall, 1),
                "end_stock": round(end, 1),
                "wasted": round(wasted, 1),
                "signal": signal,
            }
        )
    return rows


def initial_stock(rng: random.Random, h: SimHousehold, catalog: Catalog) -> Pantry:
    """A household starts with some of its staples in the cupboard (not the perishables)."""
    pantry = Pantry()
    for slug, weekly in sorted(h.daily_use.items()):
        product = catalog.products.get(slug)
        if product is None or product.perishable:
            continue
        pantry.stock[slug] = weekly * rng.uniform(0.0, 1.0)
    return pantry


def total_paise(lines: list[Line]) -> int:
    return sum(line.total_paise for line in lines)
