"""RQ1's baseline and cold-start curve, scored the way the twin will be: predict next week's
consumption of each daily-use item. The family-size rule table (M3's rule box) is the static
baseline; a running mean of the weeks observed so far is the simplest learner, and its error
after k observed weeks is the cold-start curve the twin (M-A) has to beat.

The metric is the protocol's quantity MAE as a share of the weekly quantity, per item (a
household's product): sum over its scored weeks of |prediction - actual| / sum of actual, zero
weeks kept, then the mean over items. For the rule table every week is scored; for the cold-start
curve at k observed weeks, week k + 1 is, scaled by the item's mean weekly quantity over the run.
Actual is the simulator's consumption that week (dish ingredients plus daily use); an item never
used in the run has no weekly quantity to scale by and is left out.
"""

from dataclasses import dataclass
from datetime import date

from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample
from gharbasket_sim.weeks import PLANNERS, cached, simulate

from gharbasket.modules.subscriptions import rules


@dataclass(frozen=True)
class PantryRun:
    seed: int
    households: int
    rule_table_mae: float  # the rule table predicting each week (MAE as a share of weekly qty)
    cold_start: list[float]  # the running mean of k observed weeks predicting week k + 1


def run_pantry(
    seed: int, catalog: Catalog, *, weeks: int, start: date, adoption: float | None
) -> PantryRun:
    cohorts = config.load()
    households = sample(seed, cohorts, catalog, adoption)
    plan = cached(PLANNERS["rules"])  # the dishes matter only through what they use
    rule_errors: list[float] = []
    curve_errors: list[list[float]] = [[] for _ in range(max(weeks - 1, 0))]
    for h in households:
        rule = rules.daily_use_plan(h.adult_equivalents)
        rows = simulate(seed, h, catalog, start, weeks, plan, None)
        observed: dict[str, dict[int, float]] = {slug: {} for slug in rule}
        for c in rows.consumption:
            if c["household"] == h.key and c["product"] in rule:
                observed[c["product"]][c["week"]] = c["qty"]
        for slug, by_week in observed.items():
            actuals = [by_week.get(w, 0.0) for w in range(weeks)]
            total = sum(actuals)
            if total <= 0:
                continue
            rule_errors.append(sum(abs(rule[slug] - a) for a in actuals) / total)
            for w in range(1, weeks):
                guess = sum(actuals[:w]) / w  # the running mean of the weeks observed so far
                curve_errors[w - 1].append(abs(guess - actuals[w]) / (total / weeks))
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return PantryRun(seed, len(households), mean(rule_errors), [mean(e) for e in curve_errors])
