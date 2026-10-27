# pyright: reportIndexIssue=false
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false
"""The pantry twin v1 (M10 §3.3 v1, handbook 08 M-A; RQ1): the cohort priors the online
Gamma-Poisson update starts from, refit with NumPyro from what the serving twin observes instead
of the family-size rule table.

What is observed is exactly the serving twin's observation (`subscriptions.api.
daily_use_history`): per locked box and twin product (perishable daily use: milk, curd), the
delivered quantity minus that week's planned recipes' need, never below 0, and the tap.

Model, per product, over one data source's households (never pooled across sources, ADR-0008):
- a cohort c (household size band by adult equivalents) has a weekly use per adult equivalent
  `mu_c` (in reference steps; LogNormal around the rule table's number) and a prior weight `w_c`
  (weeks; Gamma(2, 1));
- a household h in cohort c with `AE_h` adult equivalents uses `lam_h ~ Gamma(w_c mu_c AE_h,
  w_c)`: mean mu_c AE_h, weight w_c, the very prior the serving update assumes once M3 multiplies
  the stored per-equivalent mean by the household's equivalents;
- an observed week is its tap-corrected share in that observation's own steps, kept fractional,
  under the Poisson log-density the conjugate serving update uses (`alpha += sum of counts`):
  `count_i log(r_i) - r_i - log Gamma(count_i + 1)` with `r_i = lam_h x ref_step / step_i`.

What comes out per cohort x product is `prior_mean_per_ae` (base units a week per adult
equivalent) and `prior_weeks` (= w_c). Fitting is NUTS (one chain, a few hundred draws), started
from the data (each household at its own mean); a chain that never moved (acceptance near 0, the
step size collapsed) is not published: that product keeps the rule table.
"""

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import median
from typing import Any

import numpy as np
from gharbasket_ml.pantry import TAP_FACTOR

from gharbasket.modules.subscriptions import rules

MODEL_NAME = "pantry_priors"
COHORTS = ("small", "medium", "large")  # by adult equivalents: < 2, 2 to < 3.5, 3.5 and up
MIN_HOUSEHOLDS = 3  # per product and per cohort, before a learned prior is kept
WARMUP, SAMPLES = 300, 300
MIN_ACCEPT = 0.5  # NUTS adapts towards 0.8; a chain far below it did not explore the posterior
RULE_PRIOR_WEEKS = 2.0  # M3's weight on the rule table (subscriptions.service.RULE_PRIOR_WEEKS)
OBSERVED_WEEKS = 12  # M3's window (subscriptions.service.OBSERVED_WEEKS)


def cohort_of(adult_equivalents: float) -> str:
    return rules.cohort_of(adult_equivalents)


@dataclass(frozen=True)
class Observation:
    household: str
    equivalents: float  # the household's adult equivalents
    product: str  # slug
    week: int  # ordinal (delivery date as toordinal // 7, or the simulator's week)
    share: float  # the box's daily-use share in base units (delivered - recipe need, >= 0)
    signal: str | None  # ran_out | wasted | just_right | None
    step: float  # base units in one step of this observation's product
    data_source: str = "synthetic"

    @property
    def cohort(self) -> str:
        return cohort_of(self.equivalents)

    @property
    def count(self) -> float:
        """Tap-corrected steps, fractional (the serving update adds them to alpha as they are)."""
        return self.share * TAP_FACTOR[self.signal] / self.step

    @property
    def use(self) -> float:
        """Tap-corrected base units: the week's daily use as the twin reads it."""
        return self.share * TAP_FACTOR[self.signal]


@dataclass
class Prior:
    cohort: str
    product: str
    prior_mean_per_ae: float  # base units a week per adult equivalent
    prior_weeks: float
    households: int
    weeks: int
    rule_mean_per_ae: float | None = None  # the table's number, for the card


@dataclass
class Fit:
    version: str
    priors: list[Prior] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)  # product or product/cohort → why

    def by_key(self) -> dict[tuple[str, str], Prior]:
        return {(p.cohort, p.product): p for p in self.priors}


def _fit_product(
    observations: list[Observation], guess: dict[str, float], *, seed: int = 7
) -> dict[str, tuple[float, float]] | str:
    """NUTS on one product: {cohort: (mean reference steps a week per AE, prior weeks)}, or why
    the chain cannot be trusted (the product then keeps the rule table)."""
    import jax
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from jax.scipy.special import xlogy
    from numpyro.infer import MCMC, NUTS, init_to_value

    ref = median(o.step for o in observations)
    households = sorted({o.household for o in observations})
    h_index = {h: i for i, h in enumerate(households)}
    first = {o.household: o for o in reversed(observations)}
    cohorts = sorted({o.cohort for o in observations}, key=COHORTS.index)
    c_index = {c: i for i, c in enumerate(cohorts)}
    h_cohort = jnp.array([c_index[first[h].cohort] for h in households])
    h_ae = jnp.array([first[h].equivalents for h in households], dtype=jnp.float32)
    obs_h = jnp.array([h_index[o.household] for o in observations])
    counts = jnp.array([o.count for o in observations], dtype=jnp.float32)
    scale = jnp.array([ref / o.step for o in observations], dtype=jnp.float32)
    centre = jnp.array([max(guess.get(c, 1.0), 0.05) for c in cohorts])

    def model(h_cohort: Any, h_ae: Any, obs_h: Any, scale: Any, counts: Any) -> None:
        with numpyro.plate("cohort", len(cohorts)):
            mu = numpyro.sample("mu", dist.LogNormal(jnp.log(centre), 1.0))
            w = numpyro.sample("w", dist.Gamma(2.0, 1.0))
        with numpyro.plate("household", len(households)):
            lam = numpyro.sample("lam", dist.Gamma(w[h_cohort] * mu[h_cohort] * h_ae, w[h_cohort]))
        rate = lam[obs_h] * scale + 1e-9
        # The Poisson log-density without its -log Gamma(count + 1): a constant in the parameters.
        numpyro.factor("y", jnp.sum(xlogy(counts, rate) - rate))

    # Start where the data are, not at a random point: each household at its own mean weekly
    # count, each cohort at its households' mean per equivalent, w at its prior mean.
    own: dict[str, list[float]] = defaultdict(list)
    for o in observations:
        own[o.household].append(o.count * o.step / ref)
    lam0 = np.array([max(float(np.mean(own[h])), 0.1) for h in households])
    ae0 = np.asarray(h_ae, dtype=float)
    cohort0 = np.asarray(h_cohort)
    mu0 = np.array([float(np.mean((lam0 / ae0)[cohort0 == i])) for i in range(len(cohorts))])
    start = {"mu": jnp.asarray(mu0), "w": jnp.full(len(cohorts), 2.0), "lam": jnp.asarray(lam0)}

    kernel = NUTS(model, init_strategy=init_to_value(values=start))
    mcmc = MCMC(kernel, num_warmup=WARMUP, num_samples=SAMPLES, progress_bar=False)
    mcmc.run(
        jax.random.key(seed), h_cohort, h_ae, obs_h, scale, counts, extra_fields=("accept_prob",)
    )
    draws = mcmc.get_samples()
    stuck = _stuck(draws, np.asarray(mcmc.get_extra_fields()["accept_prob"]))
    if stuck:
        return stuck
    mu = np.asarray(draws["mu"]).mean(axis=0)
    w = np.asarray(draws["w"]).mean(axis=0)
    return {c: (float(mu[i]) * ref, float(w[i])) for c, i in c_index.items()}


def _stuck(draws: dict[str, Any], accept_prob: Any) -> str | None:
    """Why the chain cannot be trusted, or None. A chain whose step size collapsed never leaves
    its starting point (every draw the same, acceptance 0); its "posterior mean" is that point."""
    if float(np.mean(accept_prob)) < MIN_ACCEPT:
        return f"sampler stuck (mean acceptance {float(np.mean(accept_prob)):.2f})"
    for name in ("mu", "w"):
        if np.any(np.ptp(np.asarray(draws[name]), axis=0) == 0):
            return f"sampler stuck ({name} never moved)"
    return None


def fit(observations: list[Observation], version: str, *, seed: int = 7) -> Fit:
    """One NUTS fit per product with enough households; a product or cohort with too few keeps
    the rule table (left out of the priors, so M3 falls back to it). Callers pass one data
    source's observations."""
    sources = {o.data_source for o in observations}
    if len(sources) > 1:
        raise ValueError(f"one data source per fit, never pooled (got {sorted(sources)})")
    out = Fit(version)
    by_product: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        by_product[o.product].append(o)
    for product, mine in sorted(by_product.items()):
        households = {o.household for o in mine}
        if len(households) < MIN_HOUSEHOLDS:
            out.skipped[product] = f"{len(households)} households"
            continue
        ref = median(o.step for o in mine)
        rule = rules.DAILY_USE_PER_ADULT_WEEK.get(product)
        guess = {c: (rule / ref if rule else _empirical(mine, c, ref)) for c in COHORTS}
        fitted = _fit_product(mine, guess, seed=seed)
        if isinstance(fitted, str):
            out.skipped[product] = fitted
            continue
        for cohort, (per_ae, weeks) in fitted.items():
            members = {o.household for o in mine if o.cohort == cohort}
            if len(members) < MIN_HOUSEHOLDS:
                out.skipped[f"{product}/{cohort}"] = f"{len(members)} households"
                continue
            out.priors.append(
                Prior(
                    cohort,
                    product,
                    round(per_ae, 2),
                    round(min(max(weeks, 0.5), 52.0), 3),
                    len(members),
                    sum(1 for o in mine if o.cohort == cohort),
                    rule_mean_per_ae=rule,
                )
            )
    return out


def _empirical(mine: list[Observation], cohort: str, ref: float) -> float:
    """Mean tap-corrected use per adult equivalent in reference steps (no rule to start from)."""
    pool = [o for o in mine if o.cohort == cohort] or mine
    return max(float(np.mean([o.use / o.equivalents for o in pool])) / ref, 0.05)


def split_last_week(
    observations: Iterable[Observation],
) -> tuple[list[Observation], list[Observation]]:
    """(train, held out): each household's observations before its last observed week, and the
    ones in that week. A refit on `train` never sees what it is scored on."""
    observations = list(observations)
    last: dict[str, int] = {}
    for o in observations:
        last[o.household] = max(last.get(o.household, o.week), o.week)
    train = [o for o in observations if o.week < last[o.household]]
    held = [o for o in observations if o.week == last[o.household]]
    return train, held


def serving_prior(
    o: Observation, priors: dict[tuple[str, str], Prior] | None
) -> tuple[float, float, str] | None:
    """(prior_mean, prior_weeks, source) for a household's product, as M3 sends it: a learned
    prior per adult equivalent times the household's equivalents, else the rule table for its
    equivalents with a weight of two weeks; None when neither exists."""
    learned = priors.get((o.cohort, o.product)) if priors else None
    if learned is not None:
        return learned.prior_mean_per_ae * o.equivalents, learned.prior_weeks, "learned"
    rule = rules.daily_use_plan(o.equivalents).get(o.product)
    if rule is None:
        return None
    return rule, RULE_PRIOR_WEEKS, "rule"


def holdout_mae(
    train: list[Observation],
    held: list[Observation],
    priors: dict[tuple[str, str], Prior] | None,
) -> dict[str, float]:
    """Out-of-sample: predict each held-out week's tap-corrected daily use with the serving
    update over the household's earlier weeks (the last OBSERVED_WEEKS of them, as M3 reads
    them), starting from `priors` (fitted on `train` only) or from the rule table when None.
    A household's product with no earlier week is not scored (M3 drafts it from the rule table
    without asking the twin). MAE as a share of the true quantity, over every scored week."""
    from gharbasket_ml.pantry import posterior
    from gharbasket_ml.schemas import PantryItemIn, PantryObservation

    past: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in train:
        past[(o.household, o.product)].append(o)
    errors: list[float] = []
    truths: list[float] = []
    for last in held:
        earlier = sorted(past.get((last.household, last.product), []), key=lambda o: o.week)
        earlier = [o for o in earlier if o.week >= last.week - OBSERVED_WEEKS]
        start = serving_prior(last, priors)
        if not earlier or start is None:
            continue
        prior_mean, prior_weeks, _ = start
        item = PantryItemIn(
            product=last.product,
            step=last.step,
            prior_mean=prior_mean,
            prior_weeks=prior_weeks,
            observations=[
                PantryObservation(qty=o.share, signal=o.signal)  # pyright: ignore[reportArgumentType]
                for o in earlier
            ],
        )
        errors.append(abs(posterior(item).mean - last.use))
        truths.append(last.use)
    if not truths or sum(truths) == 0:
        return {"mae_pct": math.nan, "households_weeks": 0}
    return {"mae_pct": round(sum(errors) / sum(truths), 4), "households_weeks": len(truths)}


def evaluate(observations: list[Observation], *, seed: int = 7) -> dict[str, dict[str, float]]:
    """The candidate's out-of-sample card: refit on every household's weeks before its last,
    score both priors on the held-out last weeks."""
    train, held = split_last_week(observations)
    heldout_fit = fit(train, "holdout", seed=seed) if train else Fit("holdout")
    return {
        "rule_table": holdout_mae(train, held, None),
        "learned_priors": holdout_mae(train, held, heldout_fit.by_key()),
    }


def beats_rule_table(metrics: dict[str, dict[str, float]]) -> tuple[bool, str]:
    """The champion/challenger check `just promote` applies: the learned priors' out-of-sample
    MAE must be below the rule table's on the same held-out weeks."""
    learned = metrics.get("learned_priors", {}).get("mae_pct", math.nan)
    table = metrics.get("rule_table", {}).get("mae_pct", math.nan)
    if not (learned == learned and table == table):  # NaN: nothing was scored
        return False, "no out-of-sample score for both the learned priors and the rule table"
    if learned < table:
        return True, f"learned priors {learned:.4f} < rule table {table:.4f} (out-of-sample MAE)"
    return False, f"learned priors {learned:.4f} >= rule table {table:.4f} (out-of-sample MAE)"


def as_document(
    fit_: Fit, metrics: dict[str, dict[str, float]], data_source: str
) -> dict[str, Any]:
    return {
        "model": MODEL_NAME,
        "version": fit_.version,
        "fitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_source": data_source,
        "unit": "prior_mean_per_ae: base units a week per adult equivalent",
        "observation": "daily-use share of milk and curd (delivered - recipe need, >= 0) + tap",
        "cohorts": {
            "small": "adult equivalents < 2",
            "medium": "2 to < 3.5",
            "large": "3.5 and up",
        },
        "items": [
            {
                "cohort": p.cohort,
                "product": p.product,
                "prior_mean_per_ae": p.prior_mean_per_ae,
                "prior_weeks": p.prior_weeks,
                "households": p.households,
                "weeks": p.weeks,
                "rule_mean_per_ae": p.rule_mean_per_ae,
            }
            for p in fit_.priors
        ],
        "skipped": fit_.skipped,
        # Out-of-sample: fitted on each household's weeks before its last, scored on the last.
        "metrics": metrics,
        "metrics_split": "holdout_last_week",
    }
