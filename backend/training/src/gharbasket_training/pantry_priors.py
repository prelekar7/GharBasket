# pyright: reportIndexIssue=false
# pyright: reportAttributeAccessIssue=false, reportArgumentType=false, reportCallIssue=false
"""The pantry twin v1 (M10 §3.3 v1, handbook 08 M-A; RQ1): the cohort priors the online update
starts from, refit with NumPyro from what the serving twin observes instead of the family-size
rule table.

What is observed is exactly the serving twin's observation (`subscriptions.api.
daily_use_history`): per locked box and twin product (perishable daily use: milk, curd), the
daily-use share (delivered minus that week's recipes' need, never below 0), the delivered
quantity and the tap. As in serving (`gharbasket_ml.pantry`), only a tap says how much was used:
it puts the week's daily use in an interval (`gharbasket_ml.pantry.interval`); a box without
one is no observation.

Model, per product, over one data source's households (never pooled across sources, ADR-0008),
the serving model with its cohort parameters left free:
- a cohort c (household size band by adult equivalents) has a typical weekly use per adult
  equivalent `mu_c` (log-normal around the rule table's number) and a household spread `s_c`
  (half-normal, on the log scale);
- a household h of cohort c with `AE_h` adult equivalents has its factor `log m_h ~ N(0, s_c^2)`;
- a tapped week's use is `mu_c x AE_h x m_h x e^eps`, `eps ~ N(0, WEEK_SIGMA^2)`, and the tap's
  likelihood is the probability of its interval (interval-censored, as the serving update).

What comes out per cohort x product is `prior_mean_per_ae` (the typical weekly use per adult
equivalent, `mu_c`, base units) and `prior_weeks = WEEK_SIGMA^2 / s_c^2`: the serving prior
`log m ~ N(0, WEEK_SIGMA^2 / prior_weeks)` is then exactly `N(0, s_c^2)`. Fitting is NUTS (one
chain, a few hundred draws), started at the rule table with every household at the cohort's
typical use; a chain that never moved is not published: that product keeps the rule table.

The out-of-sample card (the promotion gate) is the held-out taps' log score: each household's
last tapped week, predicted by the serving update over its earlier boxes from either prior, as
the log of the probability the twin gave that tap's interval beforehand. A tap is the only
ground truth real data has about use (a box's quantity is the app's own choice).
"""

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import numpy as np
from gharbasket_ml.pantry import WEEK_SIGMA, interval
from gharbasket_ml.schemas import PantryObservation as ServedObservation

from gharbasket.modules.subscriptions import rules

MODEL_NAME = "pantry_priors"
COHORTS = ("small", "medium", "large")  # by adult equivalents: < 2, 2 to < 3.5, 3.5 and up
MIN_HOUSEHOLDS = 3  # tapped households per product and per cohort, before a prior is kept
# A cohort's prior is published only when the fit pins it down (set before any run, not tuned):
# its typical use known to about +-10 % (posterior sd of log mu_c), and taps from both sides.
# "Ran out" only bounds the use from below and "wasted" only from above; a cohort whose taps are
# almost all one kind leaves mu_c to the hyperprior (one-sided cohorts fitted +14 % to +63 %
# above the table on the simulator, PR #465 review).
MAX_LOG_MU_SD = 0.1
MAX_ONE_SIDED = 0.8
METRIC = "tap_log_likelihood"  # the held-out taps' mean log predictive probability: higher wins
WARMUP, SAMPLES = 300, 300
MIN_ACCEPT = 0.5  # NUTS adapts towards 0.8; a chain far below it did not explore the posterior
RULE_PRIOR_WEEKS = rules.RULE_PRIOR_WEEKS  # M3's weight on the rule table
OBSERVED_WEEKS = 12  # M3's window (subscriptions.service.OBSERVED_WEEKS)
MIN_PRIOR_WEEKS, MAX_PRIOR_WEEKS = 0.05, 52.0  # the request's bounds (and the serving floor)
LOG_EDGE = 50.0  # stands in for log 0 and log infinity in the sampler (no inf in gradients)


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
    step: float  # base units in one step of this observation's product (M3 sends it)
    data_source: str = "synthetic"
    delivered: float | None = None  # the box's quantity, recipes included (None: the share)

    @property
    def cohort(self) -> str:
        return cohort_of(self.equivalents)

    def served(self) -> ServedObservation:
        """The observation as M3 sends it to the serving twin."""
        return ServedObservation(qty=self.share, signal=self.signal, delivered=self.delivered)  # pyright: ignore[reportArgumentType]

    @property
    def bounds(self) -> tuple[float, float] | None:
        """The week's daily use as the tap bounds it (base units), or None: no tap, nothing
        learned (the serving twin's `interval`)."""
        return interval(self.served())


@dataclass
class Prior:
    cohort: str
    product: str
    prior_mean_per_ae: float  # base units a week per adult equivalent (typical, the median)
    prior_weeks: float
    households: int  # tapped households
    weeks: int  # tapped weeks
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
) -> dict[str, tuple[float, float, float]] | str:
    """NUTS on one product's tapped weeks: {cohort: (typical weekly use per AE in base units,
    prior weeks, posterior sd of log mu_c)}, or why the chain cannot be trusted (the product then
    keeps the rule table)."""
    import jax
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from jax.scipy.special import ndtr
    from numpyro.infer import MCMC, NUTS, init_to_value

    observations = [o for o in observations if o.bounds is not None]
    households = sorted({o.household for o in observations})
    h_index = {h: i for i, h in enumerate(households)}
    first = {o.household: o for o in reversed(observations)}
    cohorts = sorted({o.cohort for o in observations}, key=COHORTS.index)
    c_index = {c: i for i, c in enumerate(cohorts)}
    h_cohort = jnp.array([c_index[first[h].cohort] for h in households])
    h_log_ae = jnp.array([math.log(first[h].equivalents) for h in households], dtype=jnp.float32)
    obs_h = jnp.array([h_index[o.household] for o in observations])
    edges = [b for o in observations if (b := o.bounds) is not None]
    log_lo = jnp.array(
        [math.log(lo) if lo > 0 else -LOG_EDGE for lo, _ in edges],
        dtype=jnp.float32,
    )
    log_hi = jnp.array(
        [math.log(hi) if hi < math.inf else LOG_EDGE for _, hi in edges],
        dtype=jnp.float32,
    )
    centre = jnp.log(jnp.array([max(guess.get(c, 1.0), 1e-3) for c in cohorts]))

    def model(h_cohort: Any, h_log_ae: Any, obs_h: Any, log_lo: Any, log_hi: Any) -> None:
        with numpyro.plate("cohort", len(cohorts)):
            log_mu = numpyro.sample("log_mu", dist.Normal(centre, 1.0))
            s = numpyro.sample("s", dist.HalfNormal(0.5))
        with numpyro.plate("household", len(households)):
            z = numpyro.sample("z", dist.Normal(0.0, 1.0))
        loc = (log_mu[h_cohort] + h_log_ae + s[h_cohort] * z)[obs_h]
        a = (log_lo - loc) / WEEK_SIGMA
        b = (log_hi - loc) / WEEK_SIGMA
        # P(a < Z <= b), on the side that keeps its precision in the tails.
        p = jnp.where(a > 0, ndtr(-a) - ndtr(-b), ndtr(b) - ndtr(a))
        numpyro.factor("taps", jnp.sum(jnp.log(jnp.maximum(p, 1e-30))))

    start = {
        "log_mu": centre,
        "s": jnp.full(len(cohorts), 0.2),
        "z": jnp.zeros(len(households)),
    }
    kernel = NUTS(model, init_strategy=init_to_value(values=start))
    mcmc = MCMC(kernel, num_warmup=WARMUP, num_samples=SAMPLES, progress_bar=False)
    mcmc.run(
        jax.random.key(seed),
        h_cohort,
        h_log_ae,
        obs_h,
        log_lo,
        log_hi,
        extra_fields=("accept_prob",),
    )
    draws = mcmc.get_samples()
    stuck = _stuck(draws, np.asarray(mcmc.get_extra_fields()["accept_prob"]))
    if stuck:
        return stuck
    log_mu = np.asarray(draws["log_mu"]).mean(axis=0)
    log_mu_sd = np.asarray(draws["log_mu"]).std(axis=0)
    s2 = (np.asarray(draws["s"]) ** 2).mean(axis=0)
    return {
        c: (
            float(math.exp(log_mu[i])),
            float(WEEK_SIGMA**2 / max(s2[i], 1e-12)),
            float(log_mu_sd[i]),
        )
        for c, i in c_index.items()
    }


def _stuck(draws: dict[str, Any], accept_prob: Any) -> str | None:
    """Why the chain cannot be trusted, or None. A chain whose step size collapsed never leaves
    its starting point (every draw the same, acceptance 0); its "posterior mean" is that point."""
    if float(np.mean(accept_prob)) < MIN_ACCEPT:
        return f"sampler stuck (mean acceptance {float(np.mean(accept_prob)):.2f})"
    for name in ("log_mu", "s"):
        if np.any(np.ptp(np.asarray(draws[name]), axis=0) == 0):
            return f"sampler stuck ({name} never moved)"
    return None


def fit(observations: list[Observation], version: str, *, seed: int = 7) -> Fit:
    """One NUTS fit per product with enough tapped households; a product or cohort with too few
    keeps the rule table (left out of the priors, so M3 falls back to it). Callers pass one data
    source's observations; untapped boxes carry no information and are left out."""
    sources = {o.data_source for o in observations}
    if len(sources) > 1:
        raise ValueError(f"one data source per fit, never pooled (got {sorted(sources)})")
    out = Fit(version)
    by_product: dict[str, list[Observation]] = defaultdict(list)
    for o in observations:
        if o.bounds is not None:
            by_product[o.product].append(o)
    for product in sorted({o.product for o in observations} - set(by_product)):
        out.skipped[product] = "0 households"
    for product, mine in sorted(by_product.items()):
        households = {o.household for o in mine}
        if len(households) < MIN_HOUSEHOLDS:
            out.skipped[product] = f"{len(households)} households"
            continue
        rule = rules.DAILY_USE_PER_ADULT_WEEK.get(product)
        guess = {c: (rule if rule else _empirical(mine, c)) for c in COHORTS}
        fitted = _fit_product(mine, guess, seed=seed)
        if isinstance(fitted, str):
            out.skipped[product] = fitted
            continue
        for cohort, (per_ae, weeks, log_mu_sd) in fitted.items():
            members = {o.household for o in mine if o.cohort == cohort}
            if len(members) < MIN_HOUSEHOLDS:
                out.skipped[f"{product}/{cohort}"] = f"{len(members)} households"
                continue
            signals = [o.signal for o in mine if o.cohort == cohort]
            one_sided = max(signals.count("ran_out"), signals.count("wasted")) / len(signals)
            if one_sided > MAX_ONE_SIDED:
                out.skipped[f"{product}/{cohort}"] = f"one-sided taps ({one_sided:.0%} of one kind)"
                continue
            if log_mu_sd > MAX_LOG_MU_SD:
                out.skipped[f"{product}/{cohort}"] = f"uncertain (sd of log mu {log_mu_sd:.2f})"
                continue
            out.priors.append(
                Prior(
                    cohort,
                    product,
                    round(per_ae, 2),
                    round(min(max(weeks, MIN_PRIOR_WEEKS), MAX_PRIOR_WEEKS), 3),
                    len(members),
                    sum(1 for o in mine if o.cohort == cohort),
                    rule_mean_per_ae=rule,
                )
            )
    return out


def _empirical(mine: list[Observation], cohort: str) -> float:
    """The mean daily-use share per adult equivalent (no rule to start from)."""
    pool = [o for o in mine if o.cohort == cohort] or mine
    return max(float(np.mean([o.share / o.equivalents for o in pool])), 1e-3)


def split_last_week(
    observations: Iterable[Observation],
) -> tuple[list[Observation], list[Observation]]:
    """(train, held out): each household's observations before its last tapped week, and its
    tapped observations in that week. A refit on `train` never sees what it is scored on."""
    observations = list(observations)
    last: dict[str, int] = {}
    for o in observations:
        if o.bounds is not None:
            last[o.household] = max(last.get(o.household, o.week), o.week)
    train = [o for o in observations if o.week < last.get(o.household, math.inf)]
    held = [o for o in observations if o.week == last.get(o.household) and o.bounds is not None]
    return train, held


def serving_prior(
    o: Observation, priors: dict[tuple[str, str], Prior] | None
) -> tuple[float, float, str] | None:
    """(prior_mean, prior_weeks, source) for a household's product, as M3 sends it: a learned
    prior per adult equivalent times the household's equivalents, else the rule table for its
    equivalents with M3's weight; None when neither exists."""
    learned = priors.get((o.cohort, o.product)) if priors else None
    if learned is not None:
        return learned.prior_mean_per_ae * o.equivalents, learned.prior_weeks, "learned"
    rule = rules.daily_use_plan(o.equivalents).get(o.product)
    if rule is None:
        return None
    return rule, RULE_PRIOR_WEEKS, "rule"


def _tap_scores(
    train: list[Observation],
    held: list[Observation],
    priors: dict[tuple[str, str], Prior] | None,
) -> dict[str, list[float]]:
    """Each held-out tap's log predictive probability, by `product/cohort`: the serving update
    over the household's earlier boxes (the last OBSERVED_WEEKS weeks of them, as M3 reads them)
    from `priors` (fitted on `train` only) or from the rule table when None, and the log of the
    probability it gave the tap's interval beforehand. A household's product with no earlier
    box is not scored (M3 drafts it from the rule table without asking the twin)."""
    from gharbasket_ml.pantry import posterior
    from gharbasket_ml.schemas import PantryItemIn

    past: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in train:
        past[(o.household, o.product)].append(o)
    scores: dict[str, list[float]] = defaultdict(list)
    for last in held:
        earlier = sorted(past.get((last.household, last.product), []), key=lambda o: o.week)
        earlier = [o for o in earlier if o.week >= last.week - OBSERVED_WEEKS]
        start = serving_prior(last, priors)
        bounds = last.bounds
        if not earlier or start is None or bounds is None:
            continue
        prior_mean, prior_weeks, _ = start
        item = PantryItemIn(
            product=last.product,
            step=last.step,
            prior_mean=prior_mean,
            prior_weeks=prior_weeks,
            observations=[o.served() for o in earlier],
        )
        p = posterior(item).probability(*bounds)
        scores[f"{last.product}/{last.cohort}"].append(math.log(max(p, 1e-12)))
    return scores


def _summary(scores: list[float]) -> dict[str, float]:
    if not scores:
        return {METRIC: math.nan, "households_weeks": 0}
    return {METRIC: round(sum(scores) / len(scores), 4), "households_weeks": len(scores)}


def holdout_tap_score(
    train: list[Observation],
    held: list[Observation],
    priors: dict[tuple[str, str], Prior] | None,
) -> dict[str, float]:
    """Out-of-sample, pooled: the mean over the held-out taps of the log of the probability the
    serving twin gave each tap's interval beforehand (higher is better; a perfect twin scores
    0). See `_tap_scores`."""
    return _summary([x for xs in _tap_scores(train, held, priors).values() for x in xs])


def evaluate(observations: list[Observation], *, seed: int = 7) -> dict[str, Any]:
    """The candidate's out-of-sample card: refit on every household's weeks before its last
    tapped one, score both priors on the held-out taps, pooled and per product x cohort."""
    train, held = split_last_week(observations)
    heldout_fit = fit(train, "holdout", seed=seed) if train else Fit("holdout")
    table = _tap_scores(train, held, None)
    learned = _tap_scores(train, held, heldout_fit.by_key())
    return {
        "rule_table": _summary([x for xs in table.values() for x in xs]),
        "learned_priors": _summary([x for xs in learned.values() for x in xs]),
        "by_cohort": {
            key: {
                "rule_table": _summary(table[key]),
                "learned_priors": _summary(learned.get(key, [])),
            }
            for key in sorted(table)
        },
    }


def beats_rule_table(metrics: dict[str, Any]) -> tuple[bool, str]:
    """The champion/challenger check `just promote` applies: the learned priors' held-out tap
    log-likelihood must be above the rule table's pooled, and no product x cohort may be worse
    than the rule table on its own held-out taps (one inflated cohort must not pass inside a
    good average)."""
    learned = metrics.get("learned_priors", {}).get(METRIC, math.nan)
    table = metrics.get("rule_table", {}).get(METRIC, math.nan)
    if not (learned == learned and table == table):  # NaN: nothing was scored
        return False, "no out-of-sample score for both the learned priors and the rule table"
    worse = [
        f"{key} {ours:.4f} < {theirs:.4f}"
        for key, pair in sorted(metrics.get("by_cohort", {}).items())
        if (ours := pair["learned_priors"][METRIC]) == ours
        and (theirs := pair["rule_table"][METRIC]) == theirs
        and ours < theirs
    ]
    if learned <= table:
        return False, (
            f"learned priors {learned:.4f} <= rule table {table:.4f} (held-out tap log-likelihood)"
        )
    if worse:
        return False, "learned priors worse than the rule table on " + "; ".join(worse)
    return True, (
        f"learned priors {learned:.4f} > rule table {table:.4f} (held-out tap log-likelihood), "
        "and no product x cohort worse"
    )


def as_document(fit_: Fit, metrics: dict[str, Any], data_source: str) -> dict[str, Any]:
    return {
        "model": MODEL_NAME,
        "version": fit_.version,
        "fitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "data_source": data_source,
        "unit": "prior_mean_per_ae: typical base units a week per adult equivalent",
        "observation": "taps on milk and curd boxes, as intervals of the week's daily use "
        "(share = delivered - recipe need, >= 0; untapped boxes carry no information)",
        "week_sigma": WEEK_SIGMA,
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
        # Out-of-sample: fitted on each household's weeks before its last tap, scored on it.
        "metrics": metrics,
        "metrics_split": "holdout_last_tap",
    }
