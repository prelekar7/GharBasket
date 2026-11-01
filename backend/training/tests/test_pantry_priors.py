"""The pantry twin v1: NumPyro recovers per-adult-equivalent cohort priors from the households'
taps (the serving twin's own likelihood), the out-of-sample check against the rule table (the
held-out taps' log score), one candidate per data source, the registry document, `just
promote` (champion/challenger, the push that serving follows) and the job's read of locked
boxes, which is the serving twin's own."""

import json
import math
import random
from pathlib import Path
from typing import Any

import httpx2
import numpy as np
import pytest
from gharbasket_ml.pantry import WASTED_SHARE, WEEK_SIGMA, posterior
from gharbasket_ml.schemas import PantryItemIn
from gharbasket_training import pantry_priors
from gharbasket_training.jobs import promote
from gharbasket_training.jobs import refit_pantry as job
from gharbasket_training.lake import open_registry
from gharbasket_training.settings import Settings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

STEP = 500.0  # ml of milk in one step
PER_AE = 1000.0  # the truth: ml of milk a week per adult equivalent (the rule table says 1400)
SPREAD = 0.2  # the truth: households' spread around it, on the log scale
EQUIVALENTS = {"small": (1.0, 1.8), "medium": (2.0, 3.3), "large": (3.5, 5.0)}


def tap(use: float, box: float) -> str:
    if use > box:
        return "ran_out"
    if use <= box - WASTED_SHARE * box:
        return "wasted"
    return "just_right"


def synthetic(
    seed: int = 7,
    weeks: int = 6,
    households: int = 4,
    data_source: str = "synthetic",
    feedback: float = 0.8,
    boxes: tuple[float, float] = (1.0, 1.0),
) -> list[pantry_priors.Observation]:
    """Households of every cohort with their own adult equivalents, each using about PER_AE ml
    a week per equivalent (x its own factor, x the week's noise), boxed from the rule table
    (x a factor drawn from `boxes`, rounded up to a step) and tapping on most boxes, as the
    serving twin sees them."""
    rng = random.Random(seed)
    out = []
    for cohort, (low, high) in EQUIVALENTS.items():
        for h in range(households):
            equivalents = round(rng.uniform(low, high - 0.01), 2)
            rate = PER_AE * equivalents * math.exp(rng.gauss(0, SPREAD))
            for w in range(weeks):
                box = math.ceil(1400 * equivalents * rng.uniform(*boxes) / STEP) * STEP
                use = rate * math.exp(rng.gauss(0, WEEK_SIGMA))
                signal = tap(use, box) if rng.random() < feedback else None
                out.append(
                    pantry_priors.Observation(
                        f"{data_source}-{cohort}-{h}",
                        equivalents,
                        "milk",
                        w,
                        box,
                        signal,
                        STEP,
                        data_source,
                        delivered=box,
                    )
                )
    return out


@pytest.fixture
def quick(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pantry_priors, "WARMUP", 150)
    monkeypatch.setattr(pantry_priors, "SAMPLES", 150)


def test_the_learned_priors_beat_the_rule_table_out_of_sample(quick: None) -> None:
    """Where priors matter: new households, two boxes before the scored tap. The rule table
    says 1400 ml per adult equivalent, the households use about 1000: priors learned from the
    other households' taps (boxes on both sides of the use) predict the held-out taps better,
    pooled and in every cohort."""
    metrics = pantry_priors.evaluate(synthetic(weeks=3, households=24, boxes=(0.5, 1.1)))

    learned, table = metrics["learned_priors"], metrics["rule_table"]
    assert learned["households_weeks"] == table["households_weeks"] > 50
    assert learned[pantry_priors.METRIC] > table[pantry_priors.METRIC] + 0.1
    assert set(metrics["by_cohort"]) == {f"milk/{c}" for c in pantry_priors.COHORTS}
    assert pantry_priors.beats_rule_table(metrics)[0]


def test_one_sided_or_uncertain_cohorts_keep_the_rule_table(
    quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boxes from the rule table, 40 % above these households' use: nearly every tap says
    "wasted", an upper bound only, so the fit cannot place the cohort's use and publishes
    nothing; and a cohort whose typical use the fit leaves uncertain is not published."""
    one_sided = pantry_priors.fit(synthetic(households=8), "test")
    assert one_sided.priors == [] and len(one_sided.skipped) == 3
    why = list(one_sided.skipped.values())
    assert all(w.startswith(("one-sided taps", "uncertain")) for w in why)
    assert sum(w.startswith("one-sided taps") for w in why) >= 2

    monkeypatch.setattr(pantry_priors, "MAX_LOG_MU_SD", 0.0)
    uncertain = pantry_priors.fit(synthetic(households=8, boxes=(0.5, 1.1)), "test")
    assert uncertain.priors == [] and len(uncertain.skipped) == 3
    assert all(why.startswith("uncertain") for why in uncertain.skipped.values())


def test_the_gate_refuses_a_cohort_worse_than_the_rule_table_inside_a_good_average() -> None:
    def pair(learned: float, table: float) -> dict[str, Any]:
        return {
            "rule_table": {pantry_priors.METRIC: table, "households_weeks": 10},
            "learned_priors": {pantry_priors.METRIC: learned, "households_weeks": 10},
        }

    good = {**pair(-0.6, -0.9), "by_cohort": {"milk/small": pair(-0.5, -0.9)}}
    assert pantry_priors.beats_rule_table(good)[0]
    one_bad = {
        **pair(-0.6, -0.9),
        "by_cohort": {"milk/small": pair(-0.4, -0.9), "milk/large": pair(-1.3, -1.0)},
    }
    ok, why = pantry_priors.beats_rule_table(one_bad)
    assert not ok and "milk/large -1.3000 < -1.0000" in why


def test_a_stuck_chain_keeps_the_rule_table(quick: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """A chain whose step size collapsed returns its starting point for every draw; that is
    never published as a prior."""
    frozen = {"log_mu": np.ones((5, 3)), "s": np.full((5, 3), 0.2)}
    assert pantry_priors._stuck(frozen, np.zeros(5)) == "sampler stuck (mean acceptance 0.00)"
    assert pantry_priors._stuck(frozen, np.full(5, 0.9)) == "sampler stuck (log_mu never moved)"
    moving = {"log_mu": np.arange(15.0).reshape(5, 3), "s": np.arange(15.0).reshape(5, 3)}
    assert pantry_priors._stuck(moving, np.full(5, 0.9)) is None

    monkeypatch.setattr(pantry_priors, "_stuck", lambda draws, accept: "sampler stuck (test)")
    fitted = pantry_priors.fit(synthetic(), "test")
    assert fitted.priors == [] and fitted.skipped == {"milk": "sampler stuck (test)"}


def test_cohorts_and_what_a_tap_bounds() -> None:
    assert [pantry_priors.cohort_of(x) for x in (1.0, 1.99, 2.0, 3.49, 3.5, 6)] == [
        "small",
        "small",
        "medium",
        "medium",
        "large",
        "large",
    ]
    obs = lambda signal, delivered=None: pantry_priors.Observation(  # noqa: E731
        "h", 1.0, "milk", 0, 1000, signal, STEP, delivered=delivered
    )
    assert obs("ran_out").bounds == (1000, math.inf) and obs("ran_out").cohort == "small"
    assert obs("wasted", 1600).bounds == (0.0, 600.0)  # a quarter of the whole box was left
    assert obs("just_right", 1600).bounds == (600.0, 1000)
    assert obs(None).bounds is None  # an untapped box says nothing about use


def test_numpyro_recovers_the_per_equivalent_use_and_the_spread(quick: None) -> None:
    """With boxes on both sides of the households' use (as once the box follows the twin),
    the taps bound the use from both sides and the fit recovers it. (From rule-table boxes
    alone, 40 % above the truth here, nearly every tap is "wasted": an upper bound only.)"""
    observed = synthetic(households=20, boxes=(0.5, 1.1))

    fitted = pantry_priors.fit(observed, "test")
    by_cohort = pantry_priors.evaluate(observed)["by_cohort"]
    assert set(by_cohort) <= {f"milk/{c}" for c in pantry_priors.COHORTS}

    by_key = fitted.by_key()
    assert fitted.skipped == {} and set(by_key) == {(c, "milk") for c in pantry_priors.COHORTS}
    tapped = sum(1 for o in observed if o.signal is not None)
    assert sum(p.weeks for p in fitted.priors) == tapped
    for cohort in pantry_priors.COHORTS:  # per adult equivalent, so the same in every cohort
        prior = by_key[(cohort, "milk")]
        assert prior.prior_mean_per_ae == pytest.approx(PER_AE, rel=0.15), cohort
        # prior_weeks = WEEK_SIGMA^2 / s^2: the truth (s = 0.2) is 0.25 weeks.
        assert 0.05 <= prior.prior_weeks <= 2.0 and prior.households == 20
        assert prior.rule_mean_per_ae == 1400

    train, held = pantry_priors.split_last_week(observed)
    assert all(o.signal is not None for o in held)
    assert {o.household for o in held} == {o.household for o in observed if o.signal}
    assert all(o.week < h.week for h in held for o in train if o.household == h.household)

    document = pantry_priors.as_document(fitted, pantry_priors.evaluate(observed), "synthetic")
    assert document["model"] == "pantry_priors" and len(document["items"]) == 3
    assert document["metrics_split"] == "holdout_last_tap"
    assert document["metrics"]["learned_priors"]["households_weeks"] == len(held)


def test_the_serving_prior_scales_with_the_household_as_m3_sends_it() -> None:
    big = pantry_priors.Observation("h", 4.0, "milk", 0, 0.0, None, STEP)
    prior = pantry_priors.Prior("large", "milk", 1000.0, 3.0, 5, 20)
    weight = pantry_priors.RULE_PRIOR_WEEKS
    assert pantry_priors.serving_prior(big, {("large", "milk"): prior}) == (4000.0, 3.0, "learned")
    assert pantry_priors.serving_prior(big, None) == (1400 * 4.0, weight, "rule")
    assert pantry_priors.serving_prior(big, {("small", "milk"): prior}) == (5600.0, weight, "rule")


def test_the_holdout_scores_the_last_tap_with_the_serving_twin() -> None:
    """The household's last tapped week is held out; the score is the log of the probability
    the serving twin, given only the earlier boxes, put on that tap's interval."""
    o = lambda w, signal: pantry_priors.Observation(  # noqa: E731
        "a", 1.0, "milk", w, 1500.0, signal, STEP, delivered=1500.0
    )
    observed = [o(0, "wasted"), o(1, None), o(2, "wasted"), o(3, "ran_out"), o(4, None)]
    train, held = pantry_priors.split_last_week(observed)
    assert [x.week for x in held] == [3] and [x.week for x in train] == [0, 1, 2]
    table = pantry_priors.holdout_tap_score(train, held, None)
    before = posterior(
        PantryItemIn(
            product="milk",
            step=STEP,
            prior_mean=1400,
            prior_weeks=pantry_priors.RULE_PRIOR_WEEKS,
            observations=[x.served() for x in train],
        )
    )
    expected = math.log(before.probability(1500.0, math.inf))
    assert table == {pantry_priors.METRIC: round(expected, 4), "households_weeks": 1}
    assert expected < math.log(0.5)  # two "wasted" taps made "ran out" unlikely


def test_too_few_households_keep_the_rule_table(
    quick: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pantry_priors, "MAX_LOG_MU_SD", 1.0)  # four households: only the count
    few = [o for o in synthetic() if o.household.endswith(("-0", "-1")) and "small" in o.household]
    fitted = pantry_priors.fit(few, "test")
    assert fitted.priors == [] and fitted.skipped == {"milk": "2 households"}
    assert math.isnan(pantry_priors.holdout_tap_score([], [], None)[pantry_priors.METRIC])
    assert not pantry_priors.beats_rule_table(pantry_priors.evaluate(few))[0]
    two_large = [
        o
        for o in synthetic(boxes=(0.5, 1.1))
        if "large" not in o.household or o.household[-1] in "01"
    ]
    partial = pantry_priors.fit(two_large, "test")
    assert {p.cohort for p in partial.priors} == {"small", "medium"}
    assert partial.skipped == {"milk/large": "2 households"}
    untapped = [o for o in synthetic(feedback=0.0)]
    assert pantry_priors.fit(untapped, "test").skipped == {"milk": "0 households"}


def test_sources_are_never_pooled_and_versions_carry_the_source(
    quick: None, tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="never pooled"):
        pantry_priors.fit(synthetic() + synthetic(data_source="real"), "x")
    documents = job.candidates(
        synthetic(households=3) + synthetic(households=3, data_source="real"), "2026-10-11"
    )
    assert [(d["version"], d["data_source"]) for d in documents] == [
        ("2026-10-11-real", "real"),
        ("2026-10-11-synthetic", "synthetic"),
    ]
    registry = open_registry(Settings(), tmp_path)
    for d in documents:
        assert job.publish(registry, d) == f"pantry_priors/{d['version']}"
    assert (tmp_path / "pantry_priors/2026-10-11-real/priors.json").exists()
    assert not (tmp_path / "pantry_priors/latest.json").exists()  # the job never promotes


def _card(learned: float, table: float, version: str = "v-synthetic") -> dict[str, Any]:
    return {
        "model": "pantry_priors",
        "version": version,
        "data_source": "synthetic",
        "items": [
            {"cohort": "small", "product": "milk", "prior_mean_per_ae": 1000.0, "prior_weeks": 3.0}
        ],
        "metrics": {
            "rule_table": {pantry_priors.METRIC: table, "households_weeks": 10},
            "learned_priors": {pantry_priors.METRIC: learned, "households_weeks": 10},
        },
    }


def test_promote_checks_the_challenger_and_moves_the_pointer(tmp_path: Path) -> None:
    registry = open_registry(Settings(), tmp_path)
    job.publish(registry, _card(-0.80, -1.00, "2026-10-04-synthetic"))
    job.publish(registry, _card(-1.20, -1.00, "2026-10-11-synthetic"))

    first = promote.promote(registry, "pantry_priors", "2026-10-04-synthetic")
    assert first["version"] == "2026-10-04-synthetic" and "previous" not in first
    with pytest.raises(SystemExit, match=r"not promoted: learned priors -1\.2000 <= rule table"):
        promote.promote(registry, "pantry_priors", "2026-10-11-synthetic")
    assert json.loads((tmp_path / "pantry_priors/latest.json").read_text())["version"] == (
        "2026-10-04-synthetic"
    )
    forced = promote.promote(registry, "pantry_priors", "2026-10-11-synthetic", force=True)
    assert forced["previous"] == "2026-10-04-synthetic" and forced["why"].startswith("forced")
    with pytest.raises(SystemExit, match="not in the registry"):
        promote.promote(registry, "pantry_priors", "2030-01-01-synthetic")
    with pytest.raises(SystemExit):
        promote.promote(registry, "planner", "1")
    # A local registry without the API: --no-push moves the pointer alone (the rollback here).
    assert (
        promote.main(["pantry_priors", "2026-10-04-synthetic", "--out", str(tmp_path), "--no-push"])
        == 0
    )
    assert json.loads((tmp_path / "pantry_priors/latest.json").read_text())["version"] == (
        "2026-10-04-synthetic"
    )
    # Without --no-push the API must take the priors first; unset, nothing moves.
    with pytest.raises(SystemExit, match="API_URL and JOBS_TOKEN"):
        promote.main(["pantry_priors", "2026-10-11-synthetic", "--out", str(tmp_path), "--force"])
    assert json.loads((tmp_path / "pantry_priors/latest.json").read_text())["version"] == (
        "2026-10-04-synthetic"
    )


def test_the_ranker_challenger_must_beat_both_baselines(tmp_path: Path) -> None:
    registry = open_registry(Settings(), tmp_path)
    for version, ndcg in (("good", 0.8), ("bad", 0.5)):
        registry.lake.write_bytes(registry.key("basket_ranker", version, "model.txt"), b"m")
        registry.lake.write_bytes(
            registry.key("basket_ranker", version, "card.json"),
            json.dumps(
                {
                    "metrics": {
                        "lambdarank": {"ndcg_at_10": ndcg},
                        "repeat_last_week": {"ndcg_at_10": 0.6},
                        "top_frequency": {"ndcg_at_10": 0.55},
                    }
                }
            ).encode(),
        )
    assert promote.promote(registry, "basket_ranker", "good")["version"] == "good"
    with pytest.raises(SystemExit, match=r"lambdarank NDCG@10 0\.5000 <= best baseline 0\.6000"):
        promote.promote(registry, "basket_ranker", "bad")


@pytest.mark.anyio
async def test_promotion_pushes_the_versions_priors_before_the_pointer(tmp_path: Path) -> None:
    sent: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json={"accepted": 1, "unknown_slugs": []})

    settings = Settings(api_url="https://api.test", jobs_token=SecretStr("t"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        assert await promote.push_priors(http, settings, _card(-0.8, -1.0)) == 1
    assert sent == [
        {
            "version": "v-synthetic",
            "items": [
                {
                    "cohort": "small",
                    "product": "milk",
                    "prior_mean_per_ae": 1000.0,
                    "prior_weeks": 3.0,
                }
            ],
        }
    ]


@pytest.mark.anyio
async def test_the_job_pushes_only_out_of_sample_metrics_per_source() -> None:
    sent: list[tuple[str, dict[str, Any]]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        sent.append((request.url.path, body))
        return httpx2.Response(200, json={"accepted": len(body["items"])})

    settings = Settings(api_url="https://api.test", jobs_token=SecretStr("t"))
    document = _card(-0.8, -1.0, "v-real") | {"data_source": "real"}
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handle)) as http:
        assert await job.push_metrics(http, settings, document) == 2
    assert [path for path, _ in sent] == ["/v1/internal/intel/model-metrics"]
    items = sent[0][1]["items"]
    assert {i["model_name"] for i in items} == {"pantry_priors", "pantry_priors_rule_table"}
    assert {i["data_source"] for i in items} == {"live"}  # real households, never "mixed"
    assert {i["metric"] for i in items} == {pantry_priors.METRIC}


@pytest.mark.anyio
@pytest.mark.services
async def test_the_job_observes_exactly_what_the_serving_twin_observes(
    db_session: AsyncSession,
) -> None:
    """Parity on the same rows: the refit's observations are M3's `pantry_observations` (the
    daily-use share of milk and curd, recipe need subtracted, clipped at 0, and the tap)."""
    import uuid
    from datetime import date, timedelta

    from gharbasket_sim.run import simulate_world
    from sqlalchemy import func, select

    from gharbasket.modules.catalog import api as catalog_api
    from gharbasket.modules.identity import api as identity_api
    from gharbasket.modules.subscriptions import service
    from gharbasket.modules.subscriptions.models import BoxLine, WeeklyCycle
    from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session,
        seed=7,
        weeks=2,
        start=date(2026, 10, 5),
        planner="rules",
        adoption=0.1,
        write_db=True,
    )

    observed = await job.observations(db_session)

    assert observed and {o.product for o in observed} == {"milk", "curd"}  # the twin's scope
    assert {o.data_source for o in observed} == {"synthetic"}
    assert all(o.share >= 0 and o.step > 0 for o in observed)
    catalog = await catalog_api.plan_catalog(db_session, None)
    slug = {p.id: p.slug for p in catalog.products.values()}
    households = sorted({o.household for o in observed})
    for household in households:
        profile = await identity_api.household_plan_profile(db_session, uuid.UUID(household))
        assert profile is not None
        served = await service.pantry_observations(
            db_session, profile, catalog, date(2026, 10, 5) + timedelta(weeks=3)
        )
        mine = sorted(
            (o for o in observed if o.household == household), key=lambda o: (o.product, o.week)
        )
        expected = sorted(
            (slug[pid], share, signal or "", delivered)
            for pid, rows in served.items()
            for share, signal, delivered in rows
        )
        assert sorted((o.product, o.share, o.signal or "", o.delivered) for o in mine) == expected
    # Not whole boxes: the recipes' need is taken off what the boxes delivered.
    delivered = (
        await db_session.execute(
            select(func.sum(BoxLine.qty * BoxLine.step_qty))
            .join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id)
            .where(
                WeeklyCycle.status == "locked",
                BoxLine.product_id.in_([p for p, s in slug.items() if s in ("milk", "curd")]),
            )
        )
    ).scalar_one()
    assert sum(o.share for o in observed) < delivered
