"""The pantry digital twin, v0 (handbook 08 M-A; M10 §3.3): how much of a perishable daily-use
item a household goes through in a week, with uncertainty, from a cohort prior and the
household's taps.

What the app observes. A locked box carried `delivered` base units of the product, of which
`qty` (the daily-use share) was beyond the week's recipes. That quantity is the app's own choice
(the rule table, or the twin's last answer, rounded up to a step), so on its own it says nothing
about how much the household used: a week without a tap is not an observation of use. A tap
says where the week's use fell against the box (the tap's meaning):
- "ran out": the household used more than the share, y > qty;
- "wasted": at least WASTED_SHARE of the delivered quantity was left, y <= qty - WASTED_SHARE x
  delivered;
- "just right": in between.

Model (interval-censored, unit-free):
- next week's use is y = prior_mean x m x e^eps: `prior_mean` is the cohort's typical weekly use
  (the rule table, or the learned prior x adult equivalents), m the household's own factor,
  eps the week's noise;
- log m ~ N(0, s_h^2) and eps ~ N(0, WEEK_SIGMA^2), with s_h^2 = WEEK_SIGMA^2 / prior_weeks: a
  prior worth `prior_weeks` weeks of exactly observed use, as in the normal model;
- each tapped week multiplies the posterior of log m by the probability of its interval; the
  posterior lives on a fixed grid (no conjugate form exists for censored data).
- the 80 % interval is for next week's use: the posterior predictive is a mixture of lognormals,
  whose mean is `mean` and whose 10th and 90th percentiles are `p10` and `p90`.

Pure Python (`math` only): the gateway's fallback (`intel_gateway.fallback`) runs this very
computation, so the service and the fallback give the same answer.
"""

import math
from dataclasses import dataclass

from gharbasket_ml.schemas import (
    PantryItemIn,
    PantryItemOut,
    PantryObservation,
    PantryRequest,
    PantryResponse,
)

MODEL = "pantry-tap-censored-v0.2"
# The week-to-week spread of a household's daily use on the log scale. Chosen on simulator seeds
# 23, 29 and 31 (never the protocol's five; eval protocol change log, 1 Nov 2026).
WEEK_SIGMA = 0.1
# What "wasted" means: at least this share of the delivered quantity was left (simulator v1a).
WASTED_SHARE = 0.25
MIN_PRIOR_WEEKS = 0.05
GRID = 161  # points on log m, over +-SPAN prior standard deviations
SPAN = 6.0
BISECT = 50


def _cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / math.sqrt(2.0))


def interval(o: PantryObservation) -> tuple[float, float] | None:
    """The week's daily use (base units) the tap puts it in, or None when the week says nothing:
    no tap, or an interval the share's clipping at 0 left empty."""
    if o.signal is None:
        return None
    share = o.qty
    delivered = max(o.delivered if o.delivered is not None else share, share)
    waste_line = share - WASTED_SHARE * delivered
    if o.signal == "ran_out":
        lo, hi = share, math.inf
    elif o.signal == "wasted":
        lo, hi = 0.0, waste_line
    else:
        lo, hi = max(waste_line, 0.0), share
    if hi <= 0 or hi <= lo:
        return None
    return lo, hi


def _log_prob(lo: float, hi: float, centre: float, sigma: float) -> float:
    """log P(lo < y <= hi) for log y ~ N(centre, sigma^2), computed on the side that keeps its
    precision in the tails."""
    a = (math.log(lo) - centre) / sigma if lo > 0 else -math.inf
    b = (math.log(hi) - centre) / sigma if hi < math.inf else math.inf
    p = _cdf(-a) - _cdf(-b) if a > 0 else _cdf(b) - _cdf(a)
    return math.log(max(p, 1e-300))


@dataclass(frozen=True)
class Posterior:
    centre: float  # log of the prior's weekly use
    points: tuple[float, ...]  # log m on the grid
    weights: tuple[float, ...]  # the posterior's mass at each point (sums to 1)
    sigma: float  # the week's noise on the log scale
    taps: int

    @property
    def mean(self) -> float:
        """The predictive mean of next week's use."""
        if self.centre == -math.inf:
            return 0.0
        scale = sum(w * math.exp(u) for u, w in zip(self.points, self.weights, strict=True))
        return math.exp(self.centre + self.sigma**2 / 2) * scale

    @property
    def scale(self) -> tuple[float, float]:
        """The household's factor m on the prior: posterior mean and standard deviation."""
        first = sum(w * math.exp(u) for u, w in zip(self.points, self.weights, strict=True))
        second = sum(w * math.exp(2 * u) for u, w in zip(self.points, self.weights, strict=True))
        return first, math.sqrt(max(second - first**2, 0.0))

    def probability(self, lo: float, hi: float) -> float:
        """The predictive probability that next week's use falls in (lo, hi]: how likely the
        twin found a tap before it was given (the v1 refit's out-of-sample score)."""
        if self.centre == -math.inf:
            return 1.0 if lo <= 0 < hi else 0.0
        return sum(
            w * math.exp(_log_prob(lo, hi, self.centre + u, self.sigma))
            for u, w in zip(self.points, self.weights, strict=True)
        )

    def predictive_quantile(self, q: float) -> float:
        """Next week's use at quantile `q`: bisection on the lognormal mixture's CDF."""
        if self.centre == -math.inf:
            return 0.0
        live = [(u, w) for u, w in zip(self.points, self.weights, strict=True) if w > 1e-12]
        lo = self.centre + min(u for u, _ in live) - 8 * self.sigma
        hi = self.centre + max(u for u, _ in live) + 8 * self.sigma
        for _ in range(BISECT):
            mid = (lo + hi) / 2
            cdf = sum(w * _cdf((mid - self.centre - u) / self.sigma) for u, w in live)
            lo, hi = (mid, hi) if cdf < q else (lo, mid)
        return math.exp((lo + hi) / 2)


def posterior(item: PantryItemIn) -> Posterior:
    """The prior on log m, times each tapped week's interval probability, on a grid."""
    sigma = WEEK_SIGMA
    if item.prior_mean <= 0:
        return Posterior(-math.inf, (0.0,), (1.0,), sigma, 0)
    centre = math.log(item.prior_mean)
    spread = sigma / math.sqrt(max(item.prior_weeks, MIN_PRIOR_WEEKS))
    points = [spread * SPAN * (2 * i / (GRID - 1) - 1) for i in range(GRID)]
    logs = [-0.5 * (u / spread) ** 2 for u in points]
    taps = 0
    for o in item.observations:
        bounds = interval(o)
        if bounds is None:
            continue
        taps += 1
        logs = [
            lp + _log_prob(*bounds, centre + u, sigma) for lp, u in zip(logs, points, strict=True)
        ]
    top = max(logs)
    mass = [math.exp(lp - top) for lp in logs]
    total = sum(mass)
    return Posterior(centre, tuple(points), tuple(m / total for m in mass), sigma, taps)


def quantities(request: PantryRequest) -> PantryResponse:
    items = []
    for item in request.items:
        post = posterior(item)
        scale, scale_sd = post.scale
        items.append(
            PantryItemOut(
                product=item.product,
                mean=round(post.mean, 2),
                p10=round(post.predictive_quantile(0.10), 2),
                p90=round(post.predictive_quantile(0.90), 2),
                weeks_observed=len(item.observations),
                taps=post.taps,
                scale=round(scale, 4),
                scale_sd=round(scale_sd, 4),
            )
        )
    return PantryResponse(items=items, model=MODEL)
