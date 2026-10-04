"""RQ1's baseline and cold-start curve, scored the way the twin will be: predict next week's
consumption of each daily-use item. The family-size rule table (M3's rule box) is the static
baseline; a running mean of the weeks observed so far is the simplest learner, and its error
after k observed weeks is the cold-start curve the twin (M-A) has to beat.

MAE% = mean over household x product x predicted week of |prediction - actual| / actual, where
actual is the simulator's consumption that week (dish ingredients plus daily use).
"""

from dataclasses import dataclass
from datetime import date

from gharbasket_ml.pantry import quantities
from gharbasket_ml.schemas import PantryItemIn, PantryObservation, PantryRequest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample
from gharbasket_sim.weeks import PLANNERS, cached, simulate

from gharbasket.modules.subscriptions import rules


@dataclass(frozen=True)
class PantryRun:
    seed: int
    households: int
    rule_table_mae: float  # the rule table predicting each week (as a share of the actual)
    cold_start: list[float]  # the running mean of k observed weeks predicting week k + 1
    twin: list[float]  # the pantry twin (M-A v0) after k observed weeks predicting week k + 1


def run_pantry(
    seed: int, catalog: Catalog, *, weeks: int, start: date, adoption: float | None
) -> PantryRun:
    cohorts = config.load()
    households = sample(seed, cohorts, catalog, adoption)
    plan = cached(PLANNERS["rules"])  # the dishes matter only through what they use
    rule_errors: list[float] = []
    curve_errors: list[list[float]] = [[] for _ in range(max(weeks - 1, 0))]
    twin_errors: list[list[float]] = [[] for _ in range(max(weeks - 1, 0))]
    for h in households:
        rule = rules.daily_use_plan(h.adult_equivalents)
        rows = simulate(seed, h, catalog, start, weeks, plan, None)
        observed: dict[str, dict[int, float]] = {slug: {} for slug in rule}
        for c in rows.consumption:
            if c["household"] == h.key and c["product"] in rule:
                observed[c["product"]][c["week"]] = c["qty"]
        # What the twin sees: the delivered box (the week's lines) and the tap, per product.
        delivered: dict[str, dict[int, tuple[float, str | None]]] = {slug: {} for slug in rule}
        for p in rows.pantry:
            if p["household"] == h.key and p["product"] in rule and p["delivered"] > 0:
                delivered[p["product"]][p["week"]] = (p["delivered"], p["signal"])
        for slug, by_week in observed.items():
            product = catalog.products[slug]
            for w in range(weeks):
                actual = by_week.get(w, 0.0)
                if actual <= 0:
                    continue
                rule_errors.append(abs(rule[slug] - actual) / actual)
                if w >= 1:
                    seen = [by_week[k] for k in range(w) if k in by_week]
                    guess = sum(seen) / len(seen) if seen else rule[slug]
                    curve_errors[w - 1].append(abs(guess - actual) / actual)
                    boxes = [delivered[slug][k] for k in range(w) if k in delivered[slug]]
                    answer = quantities(
                        PantryRequest(
                            items=[
                                PantryItemIn(
                                    product=slug,
                                    step=product.step,
                                    prior_mean=rule[slug],
                                    observations=[
                                        PantryObservation(qty=q, signal=sig)  # pyright: ignore[reportArgumentType]
                                        for q, sig in boxes
                                    ],
                                )
                            ]
                        )
                    )
                    twin_errors[w - 1].append(abs(answer.items[0].mean - actual) / actual)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return PantryRun(
        seed,
        len(households),
        mean(rule_errors),
        [mean(e) for e in curve_errors],
        [mean(e) for e in twin_errors],
    )
