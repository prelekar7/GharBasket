"""The pantry digital twin, v0 (handbook 08 M-A; M10 §3.3): how much of each item a household
goes through in a week, with uncertainty, from a cohort prior and what its boxes and taps say.

Model (conjugate, so the online update is closed-form):
- the weekly use lambda of a household for one item has a Gamma prior with the cohort's mean
  (the family-size rule table today) and the weight of `prior_weeks` observed weeks;
- each observed week is the box's quantity corrected by the tap ("ran out" → they needed more,
  "wasted" → less), measured in units of one step so the Poisson likelihood is on counts;
- the posterior is Gamma again: alpha = alpha0 + sum of counts, beta = beta0 + weeks. Its mean
  is the quantity to put in the box; the 80 % interval is the Gamma's 10th and 90th percentiles.

v1 (training, NumPyro) refits the cohort priors from every household; the formulas here stay.
"""

from dataclasses import dataclass

from scipy.stats import gamma

from gharbasket_ml.schemas import PantryItemIn, PantryItemOut, PantryRequest, PantryResponse

# What a tap says about the box's quantity against the household's real use.
TAP_FACTOR = {"ran_out": 1.3, "wasted": 0.7, "just_right": 1.0, None: 1.0}
MODEL = "pantry-gamma-poisson-v0"


@dataclass(frozen=True)
class Posterior:
    alpha: float
    beta: float  # per step-count week
    step: float  # base units in one step

    @property
    def mean(self) -> float:
        return self.alpha / self.beta * self.step

    def quantile(self, q: float) -> float:
        return float(gamma.ppf(q, self.alpha, scale=1 / self.beta)) * self.step


def posterior(item: PantryItemIn) -> Posterior:
    """The closed-form update; `prior_mean` and the observations are in base units a week."""
    step = max(item.step, 1e-9)
    alpha0 = max(item.prior_weeks, 0.5) * max(item.prior_mean, 1e-9) / step
    beta0 = max(item.prior_weeks, 0.5)
    counts = sum(o.qty * TAP_FACTOR[o.signal] / step for o in item.observations)
    return Posterior(alpha0 + counts, beta0 + len(item.observations), step)


def quantities(request: PantryRequest) -> PantryResponse:
    items = []
    for item in request.items:
        post = posterior(item)
        items.append(
            PantryItemOut(
                product=item.product,
                mean=round(post.mean, 2),
                p10=round(post.quantile(0.10), 2),
                p90=round(post.quantile(0.90), 2),
                weeks_observed=len(item.observations),
                alpha=round(post.alpha, 4),
                beta=round(post.beta, 4),
            )
        )
    return PantryResponse(items=items, model=MODEL)
