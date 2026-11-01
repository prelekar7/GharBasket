"""The pantry twin (M10 §3.3): prior, taps as intervals, the predictive interval; the endpoint."""

import math
import random

import httpx2
import pytest
from gharbasket_ml import pantry
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import PantryItemIn, PantryItemOut, PantryObservation, PantryRequest
from gharbasket_ml.settings import Settings
from pydantic import SecretStr

TOKEN = "ml-test-token"
PRIOR = 1400.0


def item(
    *observations: tuple[float, str | None], prior: float = PRIOR, step: float = 500
) -> PantryItemIn:
    return PantryItemIn(
        product="milk",
        step=step,
        prior_mean=prior,
        observations=[PantryObservation(qty=q, signal=s) for q, s in observations],  # pyright: ignore[reportArgumentType]
    )


def answer(it: PantryItemIn) -> PantryItemOut:
    return pantry.quantities(PantryRequest(items=[it])).items[0]


def tap(use: float, share: float, delivered: float) -> str:
    """What the household taps after a week of `use` against the box (the tap's meaning)."""
    if use > share:
        return "ran_out"
    if use <= share - pantry.WASTED_SHARE * delivered:
        return "wasted"
    return "just_right"


def test_a_new_household_gets_its_cohort_prior_with_an_interval_around_it() -> None:
    a = answer(item())
    # log m ~ N(0, s_h^2) with s_h^2 = WEEK_SIGMA^2 / prior_weeks; the lognormal means follow.
    s_h2 = pantry.WEEK_SIGMA**2 / item().prior_weeks
    assert a.weeks_observed == 0 and a.taps == 0
    assert a.scale == pytest.approx(math.exp(s_h2 / 2), rel=1e-3)
    assert a.mean == pytest.approx(PRIOR * math.exp((s_h2 + pantry.WEEK_SIGMA**2) / 2), rel=1e-3)
    # The 80 % interval of a lognormal with the two spreads added.
    z = 1.2816 * math.sqrt(s_h2 + pantry.WEEK_SIGMA**2)
    assert a.p10 == pytest.approx(PRIOR * math.exp(-z), rel=2e-3)
    assert a.p90 == pytest.approx(PRIOR * math.exp(z), rel=2e-3)


def test_a_box_without_a_tap_says_nothing_about_use() -> None:
    """The box's quantity is the app's own choice: the defect before 1 Nov 2026 was to read it
    as the week's use, so the twin drifted towards the rule table's rounding-up with every box."""
    prior = answer(item())
    boxed = answer(item(*[(2000.0, None)] * 8))
    assert boxed.weeks_observed == 8 and boxed.taps == 0
    assert (boxed.mean, boxed.p10, boxed.p90) == (prior.mean, prior.p10, prior.p90)


def test_taps_move_the_twin_the_way_they_say() -> None:
    plain = answer(item((1500, "just_right")))
    short = answer(item((1500, "ran_out")))
    waste = answer(item((1500, "wasted")))
    assert waste.mean < plain.mean < short.mean
    assert answer(item(*[(1500, "ran_out")] * 4)).mean > short.mean  # more of the same, further


def test_the_answer_does_not_depend_on_the_pack_size() -> None:
    """The old Poisson on step counts made the interval a function of the pack size (coverage
    0.98 on the eval); the week's spread is now on the log scale."""
    small = answer(item((1500, "ran_out"), (1500, "just_right"), step=100))
    large = answer(item((1500, "ran_out"), (1500, "just_right"), step=1000))
    assert (small.mean, small.p10, small.p90) == (large.mean, large.p10, large.p90)


def test_with_a_constant_true_use_the_twin_converges_and_narrows() -> None:
    """The box follows the twin (as M3 drafts it once the twin is on) and the household taps
    every week: the prediction closes in on the household's real use and its interval narrows."""
    rng = random.Random(3)
    true_use = 1.35 * PRIOR
    observed: list[tuple[float, str | None]] = []
    errors, widths = [], []
    for _ in range(12):
        a = answer(item(*observed))
        errors.append(abs(a.mean - true_use) / true_use)
        widths.append(a.p90 - a.p10)
        box = a.mean
        use = true_use * math.exp(rng.gauss(0, pantry.WEEK_SIGMA))
        observed.append((box, tap(use, box, box)))
    assert errors[-1] < 0.08 < errors[0]
    assert max(errors[-4:]) < errors[0] / 2
    assert widths[-1] < widths[0]


def test_the_80pct_interval_covers_next_weeks_use_about_80pct_of_the_time() -> None:
    """Households drawn from the model (a factor from the prior, lognormal weeks), boxed from
    the rule table, tapping every week: after four weeks the interval holds the fifth's use
    about 80 % of the time."""
    rng = random.Random(7)
    spread = pantry.WEEK_SIGMA / math.sqrt(item().prior_weeks)
    box = 1500.0  # the rule table's 1400 rounded up to 500 ml steps
    n, hits, errors_first, errors_last = 1500, 0, 0.0, 0.0
    for _ in range(n):
        rate = PRIOR * math.exp(rng.gauss(0, spread))
        uses = [rate * math.exp(rng.gauss(0, pantry.WEEK_SIGMA)) for _ in range(5)]
        observed = [(box, tap(u, box, box)) for u in uses[:4]]
        a = answer(item(*observed))  # pyright: ignore[reportArgumentType]
        hits += a.p10 <= uses[4] <= a.p90
        errors_first += abs(answer(item()).mean - uses[4]) / uses[4]
        errors_last += abs(a.mean - uses[4]) / uses[4]
    assert 0.75 <= hits / n <= 0.85
    assert errors_last < errors_first  # four taps beat the prior alone


def test_the_wasted_line_is_against_the_whole_box() -> None:
    """A box of 1500 ml with 1000 ml for the recipes: "wasted" means at least 375 ml (a quarter
    of the box) was left, so the daily use was at most 125 ml, not at most 375 ml."""
    whole = PantryItemIn(
        product="milk",
        step=500,
        prior_mean=400,
        observations=[PantryObservation(qty=500, signal="wasted", delivered=1500)],
    )
    share_only = whole.model_copy(
        update={"observations": [PantryObservation(qty=500, signal="wasted")]}
    )
    assert answer(whole).mean < answer(share_only).mean
    assert pantry.interval(whole.observations[0]) == (0.0, 125.0)
    # A share clipped at 0 with "just right" leaves nothing to learn from.
    assert pantry.interval(PantryObservation(qty=0, signal="just_right", delivered=900)) is None


@pytest.mark.anyio
async def test_the_endpoint_answers_with_the_service_token() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = {"items": [{"product": "milk", "step": 500, "prior_mean": 1400, "observations": []}]}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        ok = await c.post(
            "/pantry/quantities", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        no = await c.post("/pantry/quantities", json=body)
    assert ok.status_code == 200 and ok.json()["items"][0]["mean"] == answer(item()).mean
    assert ok.json()["model"] == "pantry-tap-censored-v0.2" and no.status_code == 401


def test_the_predictive_probability_of_a_tap_matches_the_interval() -> None:
    post = pantry.posterior(item())
    below, above = post.probability(0.0, PRIOR), post.probability(PRIOR, math.inf)
    assert below == pytest.approx(0.5, abs=0.01) and below + above == pytest.approx(1.0)
    p10, p90 = post.predictive_quantile(0.1), post.predictive_quantile(0.9)
    assert post.probability(p10, p90) == pytest.approx(0.8, abs=0.005)


def test_a_household_far_from_its_prior_is_not_held_at_the_grid_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grid starts at +-6 prior sd (m in 0.30-3.3 for the rule prior) and widens while the
    posterior presses against its ends. A household that ran out of boxes of 3.5 times the
    rule table ten times is above 3.3 times: the answer matches a dense, wide reference grid
    (the fixed grid put its mass on the edge point and stopped there)."""
    far = item(*[(3.5 * PRIOR, "ran_out")] * 10)
    post = pantry.posterior(far)
    assert post.weights[0] + post.weights[-1] <= pantry.EDGE_MASS
    assert max(post.points) > 1.2  # wider than the first grid's +-6 sd = +-1.2
    got = answer(far)
    assert got.mean > 3.5 * PRIOR

    monkeypatch.setattr(pantry, "GRID", 4001)
    monkeypatch.setattr(pantry, "SPAN", 40.0)
    reference = answer(far)
    assert got.mean == pytest.approx(reference.mean, rel=0.01)
    assert got.p90 == pytest.approx(reference.p90, rel=0.01)


def test_ran_out_of_an_empty_share_says_nothing() -> None:
    assert pantry.interval(PantryObservation(qty=0, signal="ran_out", delivered=800)) is None
    assert answer(item((0, "ran_out"))).taps == 0
