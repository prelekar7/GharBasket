# pyright: reportIndexIssue=false
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false
"""The pantry twin v1 (M10 §3.3 v1, handbook 08 M-A; RQ1): the cohort priors the online
Gamma-Poisson update starts from, refit from every household's locked boxes and taps with
NumPyro instead of the family-size rule table.

Model, per product, over the households of all cohorts at once (hierarchical):
- a cohort c (household size band) has a weekly use `mu_c` (steps a week; LogNormal around the
  rule table's number) and a prior weight `w_c` (weeks; Gamma(2, 1), so about two weeks unless
  the data says the cohort is tighter or looser);
- a household h in cohort c uses `lam_h ~ Gamma(w_c * mu_c, w_c)`: mean mu_c, weight w_c, the
  very prior the serving update (`gharbasket_ml.pantry.posterior`) assumes;
- an observed week is Poisson(lam_h) on the tap-corrected step count.

What comes out per cohort x product is `prior_mean` (base units a week) and `prior_weeks`
(= w_c): the two numbers `POST /pantry/quantities` takes, so the formulas in the service stay.
Fitting is NUTS (one chain, a few hundred draws; seconds a product on a CPU).
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from gharbasket_ml.pantry import TAP_FACTOR

from gharbasket.modules.subscriptions import rules

MODEL_NAME = "pantry_priors"
COHORTS = ("small", "medium", "large")  # by adult equivalents: < 2, 2 to < 3.5, 3.5 and up
MIN_HOUSEHOLDS = 3  # per product, before a refit is attempted
WARMUP, SAMPLES = 300, 300


def cohort_of(adult_equivalents: float) -> str:
    if adult_equivalents < 2.0:
        return "small"
    if adult_equivalents < 3.5:
        return "medium"
    return "large"


@dataclass(frozen=True)
class Observation:
    household: str
    cohort: str
    product: str  # slug
    week: int  # ordinal (delivery date as toordinal // 7 or a running index)
    qty: float  # base units the box carried
    signal: str | None  # ran_out | wasted | just_right | None
    step: float  # base units in one step

    @property
    def count(self) -> float:
        return self.qty * TAP_FACTOR[self.signal] / self.step


@dataclass
class Prior:
    cohort: str
    product: str
    prior_mean: float  # base units a week
    prior_weeks: float
    households: int
    weeks: int
    rule_mean: float | None = None  # the table's number for an adult-equivalent cohort midpoint


@dataclass
class Fit:
    version: str
    priors: list[Prior] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # product → why

    def by_key(self) -> dict[tuple[str, str], Prior]:
        return {(p.cohort, p.product): p for p in self.priors}


def _fit_product(
    observations: list[Observation], rule_guess: dict[str, float], *, seed: int = 7
) -> dict[str, tuple[float, float]]:
    """NUTS on one product: {cohort: (mean steps a week, prior weeks)}."""
    import jax
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from numpyro.infer import MCMC, NUTS

    households = sorted({o.household for o in observations})
    h_index = {h: i for i, h in enumerate(households)}
    cohort_of_h = {o.household: o.cohort for o in observations}
    cohorts = sorted({o.cohort for o in observations}, key=COHORTS.index)
    c_index = {c: i for i, c in enumerate(cohorts)}
    h_cohort = jnp.array([c_index[cohort_of_h[h]] for h in households])
    obs_h = jnp.array([h_index[o.household] for o in observations])
    counts = jnp.array([max(0, round(o.count)) for o in observations], dtype=jnp.float32)
    guess = jnp.array([max(rule_guess.get(c, 1.0), 0.1) for c in cohorts])

    def model(h_cohort: Any, obs_h: Any, counts: Any | None = None) -> None:
        with numpyro.plate("cohort", len(cohorts)):
            mu = numpyro.sample("mu", dist.LogNormal(jnp.log(guess), 1.0))
            w = numpyro.sample("w", dist.Gamma(2.0, 1.0))
        with numpyro.plate("household", len(households)):
            lam = numpyro.sample("lam", dist.Gamma(w[h_cohort] * mu[h_cohort], w[h_cohort]))
        with numpyro.plate("obs", len(observations)):
            numpyro.sample("y", dist.Poisson(lam[obs_h]), obs=counts)

    mcmc = MCMC(NUTS(model), num_warmup=WARMUP, num_samples=SAMPLES, progress_bar=False)
    mcmc.run(jax.random.key(seed), h_cohort, obs_h, counts=counts)
    draws = mcmc.get_samples()
    mu = np.asarray(draws["mu"]).mean(axis=0)
    w = np.asarray(draws["w"]).mean(axis=0)
    return {c: (float(mu[i]), float(w[i])) for c, i in c_index.items()}


def fit(observations: list[Observation], version: str, *, seed: int = 7) -> Fit:
    """One NUTS fit per product with enough households; products with too few keep the rule
    table (left out of the priors, so the API falls back to it)."""
    out = Fit(version)
    by_product: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        by_product[o.product].append(o)
    for product, mine in sorted(by_product.items()):
        households = {o.household for o in mine}
        if len(households) < MIN_HOUSEHOLDS:
            out.skipped[product] = f"{len(households)} households"
            continue
        step = mine[0].step
        rule = rules.DAILY_USE_PER_ADULT_WEEK.get(product)
        guess = {
            c: ((rule or 0.0) * midpoint) / step if rule else _empirical(mine, c)
            for c, midpoint in (("small", 1.5), ("medium", 2.7), ("large", 4.2))
        }
        fitted = _fit_product(mine, guess, seed=seed)
        for cohort, (mean_steps, weeks) in fitted.items():
            members = {o.household for o in mine if o.cohort == cohort}
            out.priors.append(
                Prior(
                    cohort,
                    product,
                    round(mean_steps * step, 2),
                    round(min(max(weeks, 0.5), 52.0), 3),
                    len(members),
                    sum(1 for o in mine if o.cohort == cohort),
                    rule_mean=None if rule is None else round(guess[cohort] * step, 2),
                )
            )
    return out


def _empirical(mine: list[Observation], cohort: str) -> float:
    counts = [o.count for o in mine if o.cohort == cohort] or [o.count for o in mine]
    return max(float(np.mean(counts)), 0.1) if counts else 1.0


def holdout_mae(
    observations: list[Observation], priors: dict[tuple[str, str], Prior] | None
) -> dict[str, float]:
    """Leave-last-week-out: predict each household's last observed count of a product with the
    serving update from its earlier weeks, starting from the learned priors (or the rule table
    when `priors` is None); MAE as a share of the true quantity, pooled over households."""
    from gharbasket_ml.pantry import posterior
    from gharbasket_ml.schemas import PantryItemIn, PantryObservation

    by_hp: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in observations:
        by_hp[(o.household, o.product)].append(o)
    errors: list[float] = []
    truths: list[float] = []
    for (_, product), mine in by_hp.items():
        mine = sorted(mine, key=lambda o: o.week)
        if len(mine) < 2:
            continue
        past, last = mine[:-1], mine[-1]
        learned = priors.get((last.cohort, product)) if priors else None
        if learned is not None:
            prior_mean, prior_weeks = learned.prior_mean, learned.prior_weeks
        else:
            rule = rules.DAILY_USE_PER_ADULT_WEEK.get(product)
            if rule is None:
                continue
            midpoint = {"small": 1.5, "medium": 2.7, "large": 4.2}[last.cohort]
            prior_mean, prior_weeks = rule * midpoint, 2.0
        item = PantryItemIn(
            product=product,
            step=last.step,
            prior_mean=prior_mean,
            prior_weeks=prior_weeks,
            observations=[PantryObservation(qty=o.qty, signal=o.signal) for o in past],  # pyright: ignore[reportArgumentType]
        )
        truth = last.count * last.step
        errors.append(abs(posterior(item).mean - truth))
        truths.append(truth)
    if not truths or sum(truths) == 0:
        return {"mae_pct": math.nan, "households_weeks": 0}
    return {"mae_pct": round(sum(errors) / sum(truths), 4), "households_weeks": len(truths)}


def as_document(
    fit_: Fit, metrics: dict[str, dict[str, float]], data_source: str
) -> dict[str, Any]:
    return {
        "model": MODEL_NAME,
        "version": fit_.version,
        "fitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_source": data_source,
        "cohorts": {
            "small": "adult equivalents < 2",
            "medium": "2 to < 3.5",
            "large": "3.5 and up",
        },
        "items": [
            {
                "cohort": p.cohort,
                "product": p.product,
                "prior_mean": p.prior_mean,
                "prior_weeks": p.prior_weeks,
                "households": p.households,
                "weeks": p.weeks,
                "rule_mean": p.rule_mean,
            }
            for p in fit_.priors
        ],
        "skipped": fit_.skipped,
        "metrics": metrics,
    }
