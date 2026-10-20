"""RQ1: predicting next week's use of the daily-use items.

Two views, both from the simulator's ground truth:

1. The baseline (all daily-use items, the week's whole consumption): the family-size rule table
   (M3's rule box) is the static baseline; a running mean of the weeks observed so far is the
   simplest learner, and its error after k observed weeks is the cold-start curve.
2. The pantry twin's scope (M-A v0): the perishable daily-use items (milk, curd), and only their
   daily-use share, which is what the twin predicts (M3 adds the week's recipes on top). The rule
   table, the running mean of the actual daily use, and the twin, which sees only what the app
   sees: each box's daily-use share (delivered minus the planned recipes' need, the way M3
   computes it) and the household's taps. Its 80 % interval's coverage is the calibration metric.
   A reference arm (no target; eval protocol change log, 8 Oct 2026): the same twin started from
   cohort priors learned (`pantry_priors.fit`, M-A v1) on another seed's households, so the
   priors never saw the households they are scored on.

The metric is the protocol's quantity MAE as a share of the weekly quantity, per item (a
household's product): sum over its scored weeks of |prediction - actual| / sum of actual, zero
weeks kept, then the mean over items. For the rule table every week is scored; for the cold-start
curve at k observed weeks, week k + 1 is, scaled by the item's mean weekly quantity over the run.
Actual is the simulator's consumption that week (dish ingredients plus daily use; in the twin's
scope, the daily use alone); an item never used in the run has no weekly quantity to scale by and
is left out. Coverage counts every week k + 1, zero weeks too.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from gharbasket_ml.pantry import quantities
from gharbasket_ml.schemas import PantryItemIn, PantryObservation, PantryRequest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import SimHousehold, sample
from gharbasket_sim.weeks import PLANNERS, Rows, cached, simulate

from gharbasket.modules.subscriptions import rules
from gharbasket_training import pantry_priors

Priors = dict[tuple[str, str], pantry_priors.Prior]


@dataclass(frozen=True)
class PantryRun:
    seed: int
    households: int
    rule_table_mae: float  # the rule table predicting each week (MAE as a share of weekly qty)
    cold_start: list[float]  # the running mean of k observed weeks predicting week k + 1
    # The twin's scope: perishable daily-use items, their daily-use share.
    twin_rule_mae: float  # the rule table predicting each week's daily use
    twin_running_mean: list[float]  # the running mean of k weeks' daily use predicting week k + 1
    twin: list[float]  # the pantry twin (M-A v0) after k weeks predicting week k + 1
    twin_coverage: list[float]  # share of week k + 1's daily use inside the twin's 80 % interval
    # Reference only: the twin from cohort priors learned on another seed (`learned_from`).
    twin_learned: list[float] = field(default_factory=list)
    learned_from: int | None = None


def learned_priors(
    seed: int, catalog: Catalog, *, weeks: int, start: date, adoption: float | None
) -> Priors:
    """Cohort priors (per adult equivalent) fitted on seed `seed`'s households over the run's
    weeks, from what the app would see (each box's daily-use share and tap)."""
    households = sample(seed, config.load(), catalog, adoption)
    plan = cached(PLANNERS["rules"])
    observed: list[pantry_priors.Observation] = []
    for h in households:
        rule = rules.daily_use_plan(h.adult_equivalents)
        scope = [s for s in rule if s in catalog.products and catalog.products[s].perishable]
        rows = simulate(seed, h, catalog, start, weeks, plan, None)
        for slug, boxes in _twin_observations(h, catalog, rows, scope).items():
            observed.extend(
                pantry_priors.Observation(
                    h.key,
                    h.adult_equivalents,
                    slug,
                    week,
                    share,
                    signal,
                    catalog.products[slug].step,
                )
                for week, share, signal in boxes
            )
    return pantry_priors.fit(observed, f"seed-{seed}", seed=seed).by_key()


def run_pantry(
    seed: int,
    catalog: Catalog,
    *,
    weeks: int,
    start: date,
    adoption: float | None,
    priors: Priors | None = None,
    learned_from: int | None = None,
) -> PantryRun:
    """`priors` (learned on another seed, `learned_from`) adds the reference arm."""
    if priors is not None and learned_from == seed:
        raise ValueError("the learned priors must come from another seed's households")
    cohorts = config.load()
    households = sample(seed, cohorts, catalog, adoption)
    plan = cached(PLANNERS["rules"])  # the dishes matter only through what they use
    curves = max(weeks - 1, 0)
    rule_errors: list[float] = []
    curve_errors: list[list[float]] = [[] for _ in range(curves)]
    scope_rule: list[float] = []
    scope_mean: list[list[float]] = [[] for _ in range(curves)]
    twin_errors: list[list[float]] = [[] for _ in range(curves)]
    twin_hits: list[list[float]] = [[] for _ in range(curves)]
    learned_errors: list[list[float]] = [[] for _ in range(curves)]
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

        scope = [s for s in rule if s in catalog.products and catalog.products[s].perishable]
        daily = {(d["week"], d["product"]): d["qty"] for d in rows.daily_use}
        boxes = _twin_observations(h, catalog, rows, scope)
        for slug in scope:
            step = catalog.products[slug].step
            actuals = [daily.get((w, slug), 0.0) for w in range(weeks)]
            total = sum(actuals)
            if total <= 0:
                continue
            scope_rule.append(sum(abs(rule[slug] - a) for a in actuals) / total)
            for w in range(1, weeks):
                actual, scale = actuals[w], total / weeks
                guess = sum(actuals[:w]) / w
                scope_mean[w - 1].append(abs(guess - actual) / scale)
                item = PantryItemIn(
                    product=slug,
                    step=step,
                    prior_mean=rule[slug],
                    observations=[
                        PantryObservation(qty=q, signal=sig)  # pyright: ignore[reportArgumentType]
                        for k, q, sig in boxes[slug]
                        if k < w
                    ],
                )
                answer = quantities(PantryRequest(items=[item])).items[0]
                twin_errors[w - 1].append(abs(answer.mean - actual) / scale)
                twin_hits[w - 1].append(float(answer.p10 <= actual <= answer.p90))
                if priors is not None:
                    learned = priors.get((pantry_priors.cohort_of(h.adult_equivalents), slug))
                    if learned is not None:  # else M3 keeps the rule table, as above
                        item = item.model_copy(
                            update={
                                "prior_mean": learned.prior_mean_per_ae * h.adult_equivalents,
                                "prior_weeks": learned.prior_weeks,
                            }
                        )
                        answer = quantities(PantryRequest(items=[item])).items[0]
                    learned_errors[w - 1].append(abs(answer.mean - actual) / scale)
    mean = lambda xs: sum(xs) / len(xs) if xs else 0.0  # noqa: E731
    return PantryRun(
        seed,
        len(households),
        mean(rule_errors),
        [mean(e) for e in curve_errors],
        mean(scope_rule),
        [mean(e) for e in scope_mean],
        [mean(e) for e in twin_errors],
        [mean(e) for e in twin_hits],
        [mean(e) for e in learned_errors] if priors is not None else [],
        learned_from if priors is not None else None,
    )


def _twin_observations(
    h: SimHousehold, catalog: Catalog, rows: Rows, scope: list[str]
) -> dict[str, list[tuple[int, float, str | None]]]:
    """What the app would know, per product and delivered week: the box's daily-use share
    (delivered minus the planned dishes' non-optional ingredients x adult equivalents, M3's
    `recipe_need`) and the tap. The simulator's draft sums the recipes' need and the rule
    table's daily use on one line, as M3 does, so every delivered week counts."""
    need: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for m in rows.meals:
        if m["household"] == h.key and m["planned"]:
            for i in catalog.ingredients.get(m["planned"], ()):
                if not i.optional:
                    need[m["week"]][i.product] += i.qty * h.adult_equivalents
    out: dict[str, list[tuple[int, float, str | None]]] = {slug: [] for slug in scope}
    for p in rows.pantry:
        slug, w = p["product"], p["week"]
        if p["household"] != h.key or slug not in out or p["delivered"] <= 0:
            continue
        share = max(0.0, p["delivered"] - need[w][slug])
        out[slug].append((w, share, p["signal"]))
    for slug in out:
        out[slug].sort(key=lambda o: o[0])
    return out
