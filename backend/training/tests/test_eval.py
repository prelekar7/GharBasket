"""The evaluation harness: the smoke run (what CI checks), the statistics and the report."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

import httpx2
import pytest
from gharbasket_sim.catalog import from_seed_files
from gharbasket_sim.weeks import Rows
from gharbasket_training.eval import __main__ as cli
from gharbasket_training.eval import pantry_baseline, planner_ab, report
from gharbasket_training.eval.planner_ab import PlannerRun
from gharbasket_training.eval.stats import T95, estimate
from gharbasket_training.settings import Settings
from pydantic import SecretStr


def test_estimates_carry_a_t_interval() -> None:
    one = estimate([0.5])
    five = estimate([0.70, 0.72, 0.68, 0.71, 0.69])
    assert (one.mean, one.ci, one.n) == (0.5, 0.0, 1)
    assert five.n == 5 and abs(five.mean - 0.70) < 1e-9 and 0.015 < five.ci < 0.025
    assert str(five).startswith("0.700 ± 0.0")
    assert estimate([]).n == 0
    assert sorted(T95) == list(range(1, 31)) and T95[30] == 2.042
    twelve = estimate([0.0, 1.0] * 6)  # df 11: the table's t, not the normal 1.96
    sd = (12 * 0.25 / 11) ** 0.5
    assert abs(twelve.ci - 2.201 * sd / 12**0.5) < 1e-9


def _run(planner: str, seed: int, **metrics: float) -> PlannerRun:
    base = dict.fromkeys(planner_ab.METRICS, 0.0)
    return PlannerRun(planner, seed, 10, 4, {**base, **metrics})


def test_arms_are_compared_with_each_baseline_as_paired_differences() -> None:
    runs = []
    for seed, (cp, greedy, random_) in {
        7: (0.80, 0.70, 0.60),
        11: (0.78, 0.71, 0.62),
        13: (0.82, 0.69, 0.61),
    }.items():
        runs += [
            _run("cpsat", seed, acceptance=cp, distinct_perishables=6, variety=18, preference=15),
            _run("rules", seed, acceptance=cp - 0.15, distinct_perishables=9),
            _run("greedy", seed, acceptance=greedy, distinct_perishables=10, variety=12),
            _run("random", seed, acceptance=random_, distinct_perishables=12),
        ]

    diffs = planner_ab.paired(runs)

    assert set(diffs) == {"cpsat", "rules"} and set(diffs["cpsat"]) == {"greedy", "random"}
    assert next(iter(diffs["cpsat"]["greedy"])) == "acceptance"  # the primary metric first
    vs_greedy = diffs["cpsat"]["greedy"]["acceptance"]
    assert vs_greedy.n == 3 and abs(vs_greedy.mean - 0.10) < 1e-9
    # Paired: the per-seed differences (0.10, 0.07, 0.13) have sd 0.03, so t(2) x 0.03 / sqrt 3.
    assert abs(vs_greedy.ci - 4.303 * 0.03 / 3**0.5) < 1e-6
    assert abs(diffs["cpsat"]["greedy"]["perishables_fewer"].mean - 0.4) < 1e-9
    assert diffs["rules"]["greedy"]["acceptance"].mean < 0  # the fallback is behind greedy

    checks = planner_ab.against_target(planner_ab.summarise(runs), diffs)

    assert list(checks)[:3] == [
        "acceptance_ge_70pct",
        "acceptance_over_greedy",
        "acceptance_over_random",
    ]
    assert checks["acceptance_over_greedy"]["holds"] and checks["acceptance_over_random"]["holds"]
    assert checks["perishables_25pct_fewer_than_greedy"]["holds"]
    assert checks["verdict"]["holds"]

    close = {7: 0.75, 11: 0.70, 13: 0.71}  # against greedy: +0.05, -0.01, +0.02
    worse = [
        r if r.planner != "cpsat" else _run("cpsat", r.seed, acceptance=close[r.seed]) for r in runs
    ]
    checks = planner_ab.against_target(planner_ab.summarise(worse), planner_ab.paired(worse))
    assert not checks["acceptance_over_greedy"]["holds"]  # the paired interval includes 0
    assert not checks["verdict"]["holds"]


def test_v2_is_paired_with_v1_from_week_2_with_a_ci_verdict_and_guards() -> None:
    base = dict.fromkeys(planner_ab.METRICS, 0.0)

    def run(planner: str, seed: int, acceptance: float, perishables: float = 6.0) -> PlannerRun:
        # Weeks 0-1 are identical for v1 and v2; `later` holds the weeks from FROM_WEEK on.
        metrics = {**base, "acceptance": 0.75, "distinct_perishables": 6.0}
        later = {**base, "acceptance": acceptance, "distinct_perishables": perishables}
        return PlannerRun(planner, seed, 10, 4, metrics, later)

    lifts = {7: 0.02, 11: 0.01, 13: 0.03, 17: 0.02, 19: 0.015}
    runs = [
        r for s, d in lifts.items() for r in (run("cpsat", s, 0.75), run("cpsat_v2", s, 0.75 + d))
    ]

    diffs = planner_ab.paired(runs)
    assert set(diffs) == {"cpsat_v2"} and set(diffs["cpsat_v2"]) == {"cpsat"}  # no greedy here
    later = planner_ab.paired(runs, later=True, arms=("cpsat_v2",))
    checks = planner_ab.against_target(planner_ab.summarise(runs), diffs, later)

    assert checks["v2_acceptance_over_v1_all_weeks"]["value"] == 0  # diluted to nothing
    assert checks["v2_acceptance_over_v1_all_weeks"]["holds"] is None  # reference only
    assert abs(checks["v2_acceptance_over_v1"]["value"] - 0.019) < 1e-9
    assert checks["v2_acceptance_over_v1"]["holds"] and checks["v2_verdict"]["holds"]

    # A lift whose interval reaches below 0 does not hold; nor do more perishables than v1 + 1.
    mixed = {7: 0.02, 11: -0.03, 13: 0.03, 17: -0.02, 19: 0.01}
    noisy = [
        r for s, d in mixed.items() for r in (run("cpsat", s, 0.75), run("cpsat_v2", s, 0.75 + d))
    ]
    later = planner_ab.paired(noisy, later=True, arms=("cpsat_v2",))
    checks = planner_ab.against_target(planner_ab.summarise(noisy), planner_ab.paired(noisy), later)
    assert checks["v2_acceptance_over_v1"]["holds"] is False and not checks["v2_verdict"]["holds"]
    wasteful = [
        r
        for s, d in lifts.items()
        for r in (run("cpsat", s, 0.75), run("cpsat_v2", s, 0.75 + d, 7.5))
    ]
    later = planner_ab.paired(wasteful, later=True, arms=("cpsat_v2",))
    checks = planner_ab.against_target(
        planner_ab.summarise(wasteful), planner_ab.paired(wasteful), later
    )
    assert not checks["v2_perishables_within_1_of_v1"]["holds"]
    assert checks["v2_verdict"]["holds"] is False

    # Fewer than five paired seeds: no verdict; a run without week 2: no verdict either.
    three = [r for r in runs if r.seed in (7, 11, 13)]
    later = planner_ab.paired(three, later=True, arms=("cpsat_v2",))
    checks = planner_ab.against_target(planner_ab.summarise(three), planner_ab.paired(three), later)
    assert (
        checks["v2_acceptance_over_v1"]["holds"] is None and checks["v2_verdict"]["holds"] is None
    )
    short = [PlannerRun(r.planner, r.seed, 10, 2, r.metrics) for r in runs]
    later = planner_ab.paired(short, later=True, arms=("cpsat_v2",))
    checks = planner_ab.against_target(planner_ab.summarise(short), planner_ab.paired(short), later)
    assert later == {} and checks["v2_verdict"]["holds"] is None


def test_the_smoke_run_scores_every_planner_and_the_pantry_baseline(tmp_path: Path) -> None:
    catalog = from_seed_files()
    start = date(2026, 10, 5)

    runs = [
        planner_ab.run_planner(p, 7, catalog, weeks=1, start=start, adoption=0.02, shocks=None)
        for p in ("rules", "greedy", "random")
    ]
    pantry = pantry_baseline.run_pantry(7, catalog, weeks=3, start=start, adoption=0.02)

    for run in runs:
        assert run.households > 0 and set(run.metrics) == set(planner_ab.METRICS)
        assert 0 <= run.metrics["acceptance"] <= 1
        assert run.metrics["hard_rule_violations"] == 0  # every baseline keeps the hard rules
        assert run.metrics["infeasible_share"] == 0
    greedy = next(r for r in runs if r.planner == "greedy")
    random_ = next(r for r in runs if r.planner == "random")
    assert greedy.metrics["cost_paise"] <= random_.metrics["cost_paise"]  # it buys cheap
    assert 0 < pantry.rule_table_mae < 1 and len(pantry.cold_start) == 2  # weeks 2 and 3 predicted
    assert all(0 < e < 1 for e in pantry.cold_start) and len(pantry.twin) == 2
    assert 0 < pantry.twin_rule_mae < 1 and len(pantry.twin_running_mean) == 2
    assert all(0 < e < 1 for e in pantry.twin)  # milk and curd, their daily use
    assert len(pantry.twin_coverage) == 2 and all(0 <= c <= 1 for c in pantry.twin_coverage)

    built = report.build(runs, [pantry], args={"weeks": 1, "smoke": True})
    html_path, json_path = report.write(built, tmp_path)
    assert html_path.exists() and "meal planner A/B" in html_path.read_text()
    saved = json.loads(json_path.read_text())
    assert set(saved["planner"]) == {"rules", "greedy", "random"} and saved["seeds"] == [7]
    assert set(saved["planner_paired"]["rules"]) == {"greedy", "random"}
    assert saved["planner_targets"] == {}  # no CP-SAT arm in the smoke run
    # The twin's MAE target needs 5 weeks (the smoke run has 3) and the cold start is on the
    # running-mean baseline, for reference: neither has a verdict here.
    assert saved["pantry"]["target_mae_le_15pct_after_4_weeks"]["holds"] is None
    assert saved["pantry"]["cold_start_lt_4_weeks"]["holds"] is None
    assert set(saved["pantry"]["cold_start_lt_4_weeks"]["per_seed"]) == {"7"}
    prov = saved["provenance"]
    assert len(prov["cohorts_sha256"]) == 64 and "recipes.json" in prov["catalog_sha256"]
    assert prov["simulator_version"] and len(prov["simulator_source_sha256"]) == 64
    rows = report.metrics_rows(built)
    assert {r["model_name"] for r in rows} >= {
        "planner_greedy",
        "pantry_rule_table",
        "pantry_twin_v0",
    }
    assert {r["metric"] for r in rows if r["model_name"] == "pantry_twin_v0"} >= {
        "mae_pct_week_2",
        "coverage_80_week_2",
    }
    assert all(r["split"] == "holdout" and r["data_source"] == "synthetic" for r in rows)
    assert all(r["details"]["provenance"] == prov for r in rows)
    acceptance = next(
        r for r in rows if r["model_name"] == "planner_greedy" and r["metric"] == "acceptance"
    )
    assert acceptance["details"]["per_seed"] == {"7": greedy.metrics["acceptance"]}


def test_the_cli_smoke_writes_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    explanations = tmp_path / "explanations.json"
    explanations.write_text(json.dumps(RANKER_EXPLANATIONS))
    args = ["--smoke", "--out", str(tmp_path / "report"), "--ranker-explanations"]
    assert cli.main([*args, str(explanations)]) == 0
    out = capsys.readouterr().out
    assert "greedy" in out and "pantry" in out and "report:" in out
    assert "pantry twin scope" in out and "pantry twin target" in out
    assert "forecast v1 " in out and "forecast v1 - v0 wape" in out  # RQ3, 11 weeks
    page = next((tmp_path / "report").glob("*.html")).read_text()
    assert "Ranker explanations" in page and "frequency" in page and "removed_last" in page
    assert list((tmp_path / "report").glob("*.json"))


RANKER_EXPLANATIONS = {
    "method": "lightgbm pred_contrib",
    "version": "v-synthetic",
    "data": "holdout",
    "rows": 120,
    "mean_abs_shap": {"frequency": 0.6, "removed_last": 0.1},
    "mean_abs_shap_share": {"frequency": 0.857, "removed_last": 0.143},
    "decisions": [
        {
            "week": "2026-10-13",
            "product": "milk",
            "label": 1,
            "score": 1.25,
            "top_features": [{"feature": "frequency", "value": 0.9, "contribution": 0.7}],
        }
    ],
}


def test_the_report_has_no_ranker_section_without_explanations() -> None:
    assert "Ranker explanations" not in report.to_html(report.build([], [], args={}))
    empty = {**RANKER_EXPLANATIONS, "mean_abs_shap": {}}
    assert "Ranker explanations" not in report.to_html(
        report.build([], [], args={}, ranker_explanations=empty)
    )


def test_the_pantry_metric_is_mae_over_weekly_quantity_with_zero_weeks_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = SimpleNamespace(key="h1", adult_equivalents=1.0)
    actual = {"milk": [10.0, 0.0, 20.0], "curd": [5.0, 5.0, 5.0], "ghee": [0.0, 0.0, 0.0]}

    def simulate(*args: Any, **kwargs: Any) -> Rows:
        rows = Rows([], [], [])
        for slug, qtys in actual.items():
            for w, q in enumerate(qtys):
                if q:  # the simulator writes no row for a week without use
                    rows.consumption.append(
                        {"household": "h1", "week": w, "product": slug, "qty": q}
                    )
        return rows

    monkeypatch.setattr(pantry_baseline, "sample", lambda *a, **k: [h])
    monkeypatch.setattr(pantry_baseline, "simulate", simulate)
    monkeypatch.setattr(
        pantry_baseline,
        "rules",
        SimpleNamespace(daily_use_plan=lambda ae: {"milk": 10.0, "curd": 5.0, "ghee": 1.0}),
    )

    run = pantry_baseline.run_pantry(
        7, from_seed_files(), weeks=3, start=date(2026, 10, 5), adoption=0.02
    )

    # milk: (0 + 10 + 10) / 30 with its zero week kept (a MAPE would drop it: 0.25); curd: 0;
    # ghee is never used, so it has no weekly quantity to scale by.
    assert abs(run.rule_table_mae - (20 / 30 + 0) / 2) < 1e-9
    # The running mean, scaled by milk's mean week (10): week 2 |10 - 0|, week 3 |5 - 20|.
    assert run.cold_start == pytest.approx([(1.0 + 0) / 2, (1.5 + 0) / 2])


def test_the_learned_priors_reference_arm_learns_on_another_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gharbasket_training import pantry_priors

    monkeypatch.setattr(pantry_priors, "WARMUP", 100)
    monkeypatch.setattr(pantry_priors, "SAMPLES", 100)
    catalog, start = from_seed_files(), date(2026, 10, 5)
    assert cli.learned_from(7, None) == 11 and cli.learned_from(19, None) == 7
    assert cli.learned_from(5, None) == 6 and cli.learned_from(7, 7) == 11  # never itself
    assert cli.learned_from(7, 13) == 13

    fits: list[pantry_priors.Fit] = []
    fit = pantry_priors.fit

    def capture(*args: Any, **kwargs: Any) -> pantry_priors.Fit:
        fits.append(fit(*args, **kwargs))
        return fits[-1]

    monkeypatch.setattr(pantry_priors, "fit", capture)
    priors = pantry_baseline.learned_priors(11, catalog, weeks=3, start=start, adoption=0.02)

    # Three weeks of 2 % of the flats: few taps per cohort, so which cohorts the gate publishes
    # depends on posterior spreads near its threshold, which differ between platforms (x86_64
    # CI published none, arm64 one). What holds everywhere: every product x cohort is either
    # published or left on the rule table with its reason, never silently dropped.
    fitted = fits[0]
    assert priors == fitted.by_key()
    assert {product for _, product in priors} <= {"milk", "curd"}
    assert all(p.prior_mean_per_ae > 0 and 0.05 <= p.prior_weeks <= 52 for p in priors.values())
    published = {f"{p.product}/{p.cohort}" for p in fitted.priors}
    assert published or fitted.skipped  # something was decided
    assert not published & set(fitted.skipped)
    reasons = ("households", "one-sided taps", "uncertain", "sampler stuck")
    assert all(why.endswith(reasons) or why.startswith(reasons) for why in fitted.skipped.values())
    with pytest.raises(ValueError, match="another seed"):
        pantry_baseline.run_pantry(
            11, catalog, weeks=3, start=start, adoption=0.02, priors=priors, learned_from=11
        )
    run = pantry_baseline.run_pantry(
        7, catalog, weeks=3, start=start, adoption=0.02, priors=priors, learned_from=11
    )
    assert run.learned_from == 11 and len(run.twin_learned) == 2
    assert all(0 < e < 1 for e in run.twin_learned)
    if not priors:  # every cohort on the rule table: the reference arm is the rule-prior twin
        assert run.twin_learned == run.twin

    # The fallback x86_64 CI took, on every platform: nothing pinned down, nothing published,
    # each cohort's reason recorded, and the reference arm is the rule-prior twin.
    monkeypatch.setattr(pantry_priors, "MAX_LOG_MU_SD", 0.0)
    none = pantry_baseline.learned_priors(11, catalog, weeks=3, start=start, adoption=0.02)
    assert none == {} and fits[-1].skipped
    assert all(
        why.startswith(("uncertain", "one-sided taps")) or why.endswith("households")
        for why in fits[-1].skipped.values()
    )
    fallback = pantry_baseline.run_pantry(
        7, catalog, weeks=3, start=start, adoption=0.02, priors=none, learned_from=11
    )
    assert fallback.twin_learned == fallback.twin

    built = report.build([], [run], args={"weeks": 3})
    scope = built["pantry"]["twin_scope"]
    assert len(scope["twin_learned_mae"]) == 2 and scope["learned_from"] == {7: 11}
    # Reference only: the M-A target stays on the twin v0 with the rule prior.
    assert built["pantry"]["target_mae_le_15pct_after_4_weeks"]["estimator"].startswith(
        "pantry_twin_v0"
    )
    rows = [
        r for r in report.metrics_rows(built) if r["model_name"] == "pantry_twin_learned_priors"
    ]
    assert [r["metric"] for r in rows] == ["mae_pct_week_1", "mae_pct_week_2"]
    assert all(r["details"]["reference_only"] for r in rows)
    html_path, _ = report.write(built, tmp_path)
    assert "learned priors (reference)" in html_path.read_text()


@pytest.mark.anyio
async def test_metrics_are_pushed_through_the_api() -> None:
    received: dict = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        received["auth"] = request.headers["authorization"]
        received["body"] = json.loads(request.content)
        return httpx2.Response(200, json={"accepted": len(received["body"]["items"])})

    settings = Settings(api_url="https://api.test", jobs_token=SecretStr("t"))
    rows = [
        {
            "model_name": "planner_cpsat",
            "version": "abc",
            "metric": "acceptance",
            "value": 0.8,
            "split": "holdout",
            "data_source": "synthetic",
            "details": None,
        }
    ]
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        assert await report.push(http, settings, rows) == 1
    assert received["auth"] == "Bearer t" and received["body"]["items"][0]["metric"] == "acceptance"
    with pytest.raises(RuntimeError):
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
            await report.push(http, Settings(), rows)


def _week_point(lam: float | None, gini: float, cost: float, status: str = "solved") -> Any:
    from gharbasket_training.eval.assignment_sweep import SweepPoint

    return SweepPoint(
        fairness_lambda=lam,
        status=status,
        evening_status=[status] * 7,
        greedy_evenings=7 if lam is None or status == "fallback" else 0,
        seconds=0.1,
        gini=gini,
        gini_all=gini,
        cost_paise_per_portion=cost,
        rider_paise_per_portion=cost - 300,
        km_per_portion=1.0,
        kitchens_used=4,
    )


def _sweep_run(seed: int, points: dict[float | None, tuple[float, float]], **status: str) -> Any:
    """A seed's sweep: λ → (Gini, cost a portion in paise); unlisted λs copy greedy."""
    from gharbasket_training.eval.assignment_sweep import LAMBDAS, SweepRun

    greedy = points[None]
    return SweepRun(
        seed,
        7,
        30,
        90,
        [
            _week_point(lam, *points.get(lam, greedy), status=status.get(str(lam), "solved"))
            for lam in (None, *LAMBDAS)
        ],
    )


SOCIETY = (19.0300, 73.0690)


def _kitchen(name: str, lat: float, skills: tuple[str, ...] = ("veg",)) -> dict[str, Any]:
    from gharbasket_training.eval.assignment_sweep import _id

    return {"id": _id(name), "lat": lat, "lng": SOCIETY[1], "skills": list(skills), "rating": 4.5}


def _order(name: str, portions: int = 2, preferred: str | None = None) -> dict[str, Any]:
    from gharbasket_training.eval.assignment_sweep import _id

    return {
        "id": _id(name),
        "lat": SOCIETY[0],
        "lng": SOCIETY[1],
        "portions": portions,
        "skills": [],
        "preferred": preferred,
    }


def test_a_high_lambda_spreads_a_hand_built_week_over_the_kitchens() -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import Weeks

    # Two kitchens: one beside the society, one 1.7 km away. Cost alone sends every order to the
    # near one; a high λ must hand some to the far one. One week, no preferred cook.
    near, far = _kitchen("near", SOCIETY[0]), _kitchen("far", 19.0450)
    evenings = [[_order(f"o{d}-{i}") for i in range(2)] for d in range(7)]
    week = Weeks(evenings, [near, far])

    greedy = assignment_sweep.run_weeks(week, None)
    cost_only = assignment_sweep.run_weeks(week, 0.0)
    fair = assignment_sweep.run_weeks(week, 2000.0)

    assert greedy.solved and cost_only.solved and fair.solved
    assert len(fair.evening_status) == 7 and fair.greedy_evenings == 0
    assert greedy.greedy_evenings == 7 and greedy.preferred_share == 0
    assert cost_only.gini == greedy.gini == 0.5 and cost_only.kitchens_used == 1
    assert fair.gini < cost_only.gini and fair.kitchens_used == 2
    assert fair.cost_paise_per_portion > cost_only.cost_paise_per_portion
    # The quality penalty is ₹2.50 a portion (half a star below 5); the rest is the rider.
    assert cost_only.cost_paise_per_portion - cost_only.rider_paise_per_portion == 250


def test_the_rolling_earnings_carry_over_the_weeks_and_drop_out_after_28_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import RATE, Evening, Weeks

    # Five weeks, one 1-portion order a night, always cooked by `a`: each night sees the
    # portions of the 28 nights before it, never more, and never tonight's.
    a, b = _kitchen("a", SOCIETY[0]), _kitchen("b", 19.0450)
    weeks = Weeks([[_order(f"o{d}", portions=1)] for d in range(35)], [a, b])
    seen: list[dict[str, int]] = []

    def fake(orders: list[Any], kitchens: Any, rolling: dict[str, int], lam: Any) -> Evening:
        seen.append(rolling)
        return Evening([(orders[0]["id"], a["id"], 1)], 0, {a["id"], b["id"]}, "solved", False, 0)

    monkeypatch.setattr(assignment_sweep, "evening", fake)
    point = assignment_sweep.run_weeks(weeks, 300.0)

    assert [r[a["id"]] // RATE for r in seen] == [min(d, 28) for d in range(35)]
    assert all(r[b["id"]] == 0 for r in seen)
    assert point.gini == 0.5 and point.kitchens_used == 1  # the scored week: all to `a`
    assert len(point.evening_status) == 35


def test_the_earnings_of_the_warm_up_weeks_steer_the_scored_week() -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import Weeks

    # Week 1: Jain orders only the near kitchen can cook. Week 2 (scored): plain orders. With
    # week 1's earnings carried in, a high λ hands the far kitchen most of week 2 to catch her
    # up, so week 2 alone is less even than without that history.
    near = _kitchen("near", SOCIETY[0], ("veg", "jain"))
    far = _kitchen("far", 19.0450)
    jain = [[{**_order(f"j{d}-{i}"), "skills": ["jain"]} for i in range(2)] for d in range(7)]
    plain = [[_order(f"p{d}-{i}") for i in range(2)] for d in range(7)]

    def scored(evenings: list[Any]) -> Any:
        point = assignment_sweep.run_weeks(Weeks(evenings, [near, far]), 2000.0)
        assert point.solved
        return point

    alone, carried = scored(plain), scored(jain + plain)
    assert alone.gini < 0.05  # an even split of week 2
    assert carried.gini > alone.gini  # the far kitchen took most of it
    assert carried.gini_rolling < scored(jain).gini_rolling  # and the 28 days are evener


def test_the_preferred_cook_takes_her_orders_before_the_solver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gharbasket_ml import assignment
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import MAX_KM, RATE, TIME_LIMIT_S

    near, far = _kitchen("near", SOCIETY[0]), _kitchen("far", 19.0450)
    away = _kitchen("away", 19.1000)  # 7.8 km: out of reach, so her order goes to the solver
    orders = [
        _order("to-far", portions=3, preferred=far["id"]),
        _order("to-away", portions=2, preferred=away["id"]),
        _order("free", portions=2),
    ]
    sent: list[Any] = []
    solve = assignment.assign

    def spy(req: Any, *, deterministic: bool = True) -> Any:
        sent.append(req)
        return solve(req, deterministic=deterministic)

    monkeypatch.setattr(assignment, "assign", spy)
    rolling = {near["id"]: 0, far["id"]: 6000, away["id"]: 0}
    night = assignment_sweep.evening(orders, [near, far, away], rolling, 300.0)

    assert (orders[0]["id"], far["id"], 3) in night.matches and night.preferred == 3
    assert night.eligible == {near["id"], far["id"]}  # `away` can take no order
    (req,) = sent
    assert sorted(o.id for o in req.orders) == sorted([orders[1]["id"], orders[2]["id"]])
    kitchens = {k.id: k for k in req.kitchens}
    # The preferred match lowered her room and raised her rolling earnings, as in production.
    assert kitchens[far["id"]].max_portions == 8 - 3
    assert kitchens[far["id"]].earnings_paise == 6000 + 3 * RATE
    assert req.max_km == MAX_KM == 5.0 and req.time_limit_s == TIME_LIMIT_S
    assert req.fairness_lambda == 300.0 and req.paise_per_portion == RATE
    assert {o for o, _, _ in night.matches} == {o["id"] for o in orders}

    # Every order with a preferred cook who can take it: no request at all.
    sent.clear()
    night = assignment_sweep.evening(orders[:1], [near, far], rolling, 300.0)
    assert not sent and night.status == "solved" and night.preferred == 3


def test_an_order_out_of_every_kitchens_reach_is_left_out_not_a_solver_failure() -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import Weeks

    near, far = _kitchen("near", SOCIETY[0]), _kitchen("far", 19.0450)
    remote = {**_order("remote", portions=2), "lat": 19.1200}  # 10 km: no kitchen reaches it
    # Evening 1: one plain order and the remote one; evening 2: only the remote one.
    evenings = [[_order("o1", portions=2), remote], [{**remote, "id": _order("r2")["id"]}]]
    point = assignment_sweep.run_weeks(Weeks(evenings, [near, far]), 300.0)

    assert point.evening_status == ["partial", "infeasible"] and point.status == "partial"
    assert point.greedy_evenings == 0 and point.solved  # CP-SAT never fell back
    assert point.unassigned_share == round(4 / 6, 4)  # 4 of the 6 ordered portions
    assert point.cost_paise_per_portion > 0  # over the 2 assigned portions


def test_the_cost_counts_only_the_orders_every_arm_assigned() -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import Weeks

    # `big` (6 portions) can go to either kitchen, `south` (3) only to `near` (4.5 km; `far` is
    # 5.5 km away). Greedy puts `big` at `near`, the cheaper, and has no room left for `south`;
    # CP-SAT sends `big` north and `south` to `near`. So the arms leave out different orders.
    near, far = _kitchen("near", SOCIETY[0]), _kitchen("far", 19.0390)
    big = _order("big", portions=6)
    south = {**_order("south", portions=3), "lat": 18.9895}
    weeks = Weeks([[big, south]], [near, far])

    alone_greedy = assignment_sweep.run_weeks(weeks, None)
    alone_fair = assignment_sweep.run_weeks(weeks, 0.0)
    assert alone_greedy.unassigned_share == round(3 / 9, 4) and alone_fair.unassigned_share == 0
    # Each over its own orders, CP-SAT would look dearer only for cooking `south` at all.
    assert alone_fair.cost_paise_per_portion > alone_greedy.cost_paise_per_portion

    (greedy, fair), costed = assignment_sweep.sweep_points(weeks, (0.0,))
    assert costed == 6  # `big` alone: the order both arms assigned
    assert fair.solved and greedy.unassigned_share > fair.unassigned_share
    # `big` at `far` (1 km) against `near` (beside it): CP-SAT is dearer on the same order.
    assert greedy.km_per_portion == 0 and round(fair.km_per_portion) == 1
    assert fair.cost_paise_per_portion - greedy.cost_paise_per_portion == pytest.approx(
        fair.rider_paise_per_portion - greedy.rider_paise_per_portion
    )


def test_the_preference_models() -> None:
    from collections import defaultdict

    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import SweepConfig

    catalog = from_seed_files()

    def favourites(cfg: SweepConfig) -> tuple[dict[str, set[str | None]], list[Any]]:
        weeks = assignment_sweep.instance(7, catalog, 0.05, cfg)
        assert weeks is not None
        seen: dict[str, set[str | None]] = defaultdict(set)
        for o in (o for e in weeks.evenings for o in e):
            seen[o["household"]].add(o["preferred"])
        return seen, weeks.kitchens

    sticky, _ = favourites(SweepConfig(weeks=2))
    assert all(len(cooks) == 1 and None not in cooks for cooks in sticky.values())
    nightly, _ = favourites(SweepConfig(weeks=2, preferences="nightly"))
    assert any(len(cooks) > 1 for cooks in nightly.values())
    none, _ = favourites(SweepConfig(weeks=2, preferred_share=0.0))
    assert all(cooks == {None} for cooks in none.values())
    half, _ = favourites(SweepConfig(weeks=2, preferred_share=0.5))
    assert any(None in cooks and len(cooks) == 2 for cooks in half.values())
    # Popular: one favourite per household, drawn with the popularity weights (not uniformly).
    popular, _ = favourites(SweepConfig(weeks=2, preferences="popular"))
    assert popular != sticky and set(popular) == set(sticky)
    assert all(len(cooks) == 1 for cooks in popular.values())


def test_the_harness_uses_the_production_numbers() -> None:
    from gharbasket_training.eval import assignment_sweep

    from gharbasket.modules.partners import api as _partners  # noqa: F401 (import order)
    from gharbasket.modules.partners import assignments, matching

    assert assignments.ROLLING.days == assignment_sweep.ROLLING_DAYS
    assert assignment_sweep.TIME_LIMIT_S == assignments.TIME_LIMIT_S
    assert assignment_sweep.MAX_KM == matching.MAX_KM
    assert assignment_sweep.RATING_FLOOR == matching.RATING_FLOOR
    assert set(assignment_sweep.PROTOCOL_LAMBDAS) <= set(assignment_sweep.LAMBDAS)
    assert {400.0, 600.0, 800.0, 900.0, 1100.0, 1200.0} <= set(assignment_sweep.LAMBDAS)


def test_the_production_lambda_is_the_same_everywhere() -> None:
    from gharbasket_ml.schemas import FAIRNESS_LAMBDA, AssignRequest

    from gharbasket.modules.intel_gateway.schemas import AssignRequest as GatewayRequest
    from gharbasket.platform.settings import Settings as ApiSettings

    api = ApiSettings.model_fields["partners_fairness_lambda"].default
    gateway = GatewayRequest.model_fields["fairness_lambda"].default
    ml = AssignRequest.model_fields["fairness_lambda"].default
    # 600: the owner's departure from the rule's 900 (eval protocol §7, 2026-11-01).
    assert api == gateway == ml == FAIRNESS_LAMBDA == 600.0


def test_the_rule_lambda_follows_the_rule_fixed_before_the_sweep() -> None:
    from gharbasket_training.eval.assignment_sweep import KEEP_LAMBDA
    from gharbasket_training.eval.assignment_sweep import rule_lambda as production_lambda

    def grid(**rows: tuple[float, bool | None]) -> dict[str, Any]:
        return {
            k.removeprefix("l"): {"extra_cost": {"mean": cost}, "holds": holds}
            for k, (cost, holds) in rows.items()
        }

    # The target holds: the smallest λ > 0 that meets it, whatever the cost budget allows.
    holding = grid(l0=(-0.04, False), l300=(0.0, False), l700=(0.02, True), l900=(0.03, True))
    assert production_lambda(holding, True) == 700.0
    # It does not: the last λ before the first whose mean cost is above +5 %, even when a
    # larger λ dips below it again; λ = 0 never counts.
    knee = grid(
        l0=(-0.04, False),
        l100=(-0.04, False),
        l500=(0.01, False),
        l600=(0.049, False),
        l700=(0.06, False),
        l800=(0.04, False),
    )
    assert production_lambda(knee, False) == 600.0
    # Even the smallest λ > 0 costs more than +5 %: production's λ of the registration stays.
    assert production_lambda(grid(l0=(0.0, False), l100=(0.06, False)), False) == KEEP_LAMBDA
    assert KEEP_LAMBDA == 300.0
    assert production_lambda(knee, None) is None  # no verdict, no pick


def test_the_m_g_target_needs_both_upper_bounds_five_seeds_and_a_positive_lambda() -> None:
    from gharbasket_training.eval import assignment_sweep

    def target(runs: list[Any]) -> dict[str, Any]:
        return assignment_sweep.against_target(runs)["gini_le_0_25_at_le_5pct_cost"]

    def seeds(cost_300: float, n: int = 5, zero_gini: float = 0.6) -> list[Any]:
        return [
            _sweep_run(
                s,
                {
                    None: (0.6, 1000.0),
                    0.0: (zero_gini, 1000.0),
                    100.0: (0.4, 1010.0),
                    300.0: (0.2 + 0.002 * s, cost_300 + s),
                },
            )
            for s in range(n)
        ]

    holds = target(seeds(1020.0))
    assert holds["holds"] is True and holds["lambda"] == 300.0 and holds["n"] == 5
    assert holds["per_lambda"]["300"]["holds"] is True
    assert holds["per_lambda"]["100"]["holds"] is False  # Gini 0.4
    assert holds["per_lambda"]["300"]["extra_cost_upper"] <= 0.05
    assert holds["dropped_seeds"] == [] and holds["greedy_gini"]["mean"] == 0.6
    assert holds["rule_lambda"] == 300.0 and "mean-cost budget" in holds["lambda_rule"]

    costly = target(seeds(1100.0))  # +10 % cost
    assert costly["holds"] is False and costly["lambda"] is None
    # The knee: 200 (a copy of greedy here) is the last λ before 300, which costs +10 %.
    assert costly["rule_lambda"] == 200.0

    # λ = 0 is the cost-only plan: even a fair cost-only week does not meet the target.
    zero = target(seeds(1100.0, zero_gini=0.1))
    assert zero["per_lambda"]["0"]["holds"] is False and zero["holds"] is False

    few = target(seeds(1020.0, n=3))
    assert few["holds"] is None and few["lambda"] is None
    assert few["rule_lambda"] is None
    assert all(v["holds"] is None for v in few["per_lambda"].values())

    # A seed where CP-SAT fell back to greedy at one λ is dropped for every λ, and named; one
    # where it left out an order no kitchen in reach had room for is not.
    runs = seeds(1020.0)
    runs[3] = _sweep_run(3, {None: (0.6, 1000.0)}, **{"700.0": "partial"})
    assert target(runs)["dropped_seeds"] == []
    runs[2] = _sweep_run(2, {None: (0.6, 1000.0)}, **{"700.0": "fallback"})
    dropped = target(runs)
    assert dropped["dropped_seeds"] == [2] and dropped["n"] == 4 and dropped["holds"] is None
    summary = assignment_sweep.summarise(runs)
    assert all(m["gini"].n == 4 for m in summary.values())
    assert summary["300"]["extra_cost"].n == 4 and "extra_cost" not in summary["greedy"]


def test_the_sweep_table_shows_rupees_not_paise() -> None:
    runs = [_sweep_run(s, {None: (0.6, 1234.0), 300.0: (0.3, 1300.0)}) for s in (7, 11)]
    built = report.build([], [], args={"smoke": True}, sweep_runs=runs)
    page = report.to_html(built)

    assert "cost a portion (distance + quality penalty, ₹)" in page
    assert "<td>12.34 ± 0.00</td>" in page and "<td>13.00 ± 0.00</td>" in page  # paise ÷ 100
    assert "<td>9.34 ± 0.00</td>" in page  # the rider's part
    assert "1234" not in page
    assert "no verdict: 2 paired seed(s)" in page


def test_the_lambda_sweep_solves_every_point_of_a_small_week() -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import SweepConfig

    catalog = from_seed_files()
    cfg = SweepConfig(lambdas=(0.0, 300.0, 2000.0), weeks=2)
    run = assignment_sweep.run_sweep(7, catalog, adoption=0.02, cfg=cfg)

    assert run is not None and run.orders > 0 and run.portions >= run.orders
    assert run.config == cfg and 0 < run.evenings <= 7
    assert [p.fairness_lambda for p in run.points] == [None, 0.0, 300.0, 2000.0]
    assert all(p.solved and set(p.evening_status) == {"solved"} for p in run.points)
    assert all(len(p.evening_status) > 7 for p in run.points)  # both weeks were lived
    assert all(p.greedy_evenings == 0 for p in run.points[1:])
    assert all(0 <= p.gini <= 1 and p.cost_paise_per_portion > 0 for p in run.points)
    # Every tiffin names its favourite cook, who takes it first whenever she can: the same
    # orders in every arm.
    shares = {p.preferred_share for p in run.points}
    assert len(shares) == 1 and 0 < shares.pop() <= 1

    built = report.build([], [], args={"smoke": True}, sweep_runs=[run])
    assert built["seeds"] == [7] and set(built["assignment"]) == {"greedy", "0", "300", "2000"}
    prov = built["provenance"]["assignment"]
    assert prov["lambdas"] == [0.0, 300.0, 2000.0] and prov["ortools_version"]
    assert prov["weeks"] == 2 and prov["rolling_days"] == 28 and prov["max_km"] == 5.0
    assert prov["model"] == "assignment-cpsat-v0" and len(prov["societies_sha256"]) == 64
    target = built["assignment_targets"]["gini_le_0_25_at_le_5pct_cost"]
    assert target["holds"] is None and target["seeds"] == [7] and target["dropped_seeds"] == []
    assert set(target["per_lambda"]) == {"0", "300", "2000"}
    assert target["rule_lambda"] is None  # one seed: no verdict, no pick
    rows = report.metrics_rows(built)
    assert {r["model_name"] for r in rows} >= {"assignment_cpsat", "assignment_greedy"}
    extra = next(r for r in rows if r["metric"] == "extra_cost_lambda_2000")
    assert set(extra["details"]["per_seed"]) == {"7"} and extra["details"]["dropped_seeds"] == []
    page = report.to_html(built)
    assert "λ sweep" in page and "preferred cook" in page and "rule fixed before" in page


def test_the_cli_passes_the_sweep_flags_and_checks_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gharbasket_training.eval import assignment_sweep

    got: list[Any] = []

    def fake(seed: int, catalog: Any, *, adoption: float | None, cfg: Any) -> None:
        got.append((seed, adoption, cfg))

    monkeypatch.setattr(assignment_sweep, "run_sweep", fake)
    monkeypatch.setattr(cli.planner_ab, "run_planner", lambda *a, **k: None)
    monkeypatch.setattr(cli.pantry_baseline, "run_pantry", lambda *a, **k: None)
    monkeypatch.setattr(cli.report, "build", lambda *a, **k: {"args": k["args"]})
    monkeypatch.setattr(cli.report, "write", _stop)
    flags = ["--seeds", "7", "11", "--no-learned", "--no-forecast", "--no-routing"]
    flags += ["--out", str(tmp_path)]
    sweep = ["--assign-lambdas", "700", "0", "700", "--assign-weeks", "3"]
    sweep += ["--assign-kitchens", "8", "--assign-preferred-share", "0.5"]
    sweep += ["--assign-preferences", "nightly"]
    with pytest.raises(_Stop) as stop:
        cli.main([*flags, *sweep])

    cfg = assignment_sweep.SweepConfig(
        (0.0, 700.0), weeks=3, kitchens=8, preferred_share=0.5, preferences="nightly"
    )
    assert got == [(7, None, cfg), (11, None, cfg)]
    args = stop.value.args[0]["args"]
    assert args["assign_lambdas"] == [0.0, 700.0] and args["assign_weeks"] == 3
    assert args["assign_kitchens"] == 8 and args["assign_preferred_share"] == 0.5
    assert args["assign_preferences"] == "nightly"

    got.clear()
    with pytest.raises(_Stop):
        cli.main(flags)  # the defaults are the protocol's
    assert got[0][2] == assignment_sweep.SweepConfig()
    assert got[0][2].lambdas == assignment_sweep.LAMBDAS and got[0][2].weeks == 5

    for bad in (
        ["--assign-weeks", "0"],
        ["--assign-kitchens", "0"],
        ["--assign-lambdas", "-1"],
        ["--assign-preferred-share", "1.5"],
        ["--assign-preferences", "random"],
    ):
        with pytest.raises(SystemExit):
            cli.main([*flags, *bad])


class _Stop(Exception):
    pass


def _stop(built: dict[str, Any], out: Path) -> NoReturn:
    raise _Stop(built)


def test_the_twin_sees_the_share_m3_computes_after_a_meal_swap() -> None:
    """M3 stores a swapped meal in the cycle's meal plan and redraws the box for it (`set_meal`),
    so the daily-use share it sends the twin subtracts the swapped-in dish's need. Before 1 Nov
    2026 the harness subtracted the planned dish's need instead (288 of 798 milk and curd weeks
    on seed 7 differed)."""
    ing = SimpleNamespace
    catalog = SimpleNamespace(
        ingredients={
            "kheer": [ing(product="milk", qty=300.0, optional=False)],
            "poha": [ing(product="milk", qty=50.0, optional=True)],
        }
    )
    h = SimpleNamespace(key="h1", adult_equivalents=2.0)
    rows = Rows([], [], [])
    rows.meals.append({"household": "h1", "week": 0, "planned": "poha", "final": "kheer"})
    rows.meals.append({"household": "h1", "week": 1, "planned": "kheer", "final": "poha"})
    for week, delivered, signal in ((0, 3500.0, "ran_out"), (1, 3000.0, None)):
        rows.pantry.append(
            {
                "household": "h1",
                "week": week,
                "product": "milk",
                "delivered": delivered,
                "signal": signal,
            }
        )
    seen = pantry_baseline._twin_observations(h, catalog, rows, ["milk"])  # pyright: ignore[reportPrivateUsage, reportArgumentType]
    # Week 0: the swapped-in kheer took 600 ml of the 3500; week 1: poha's milk is optional.
    assert seen["milk"] == [(0, 2900.0, "ran_out", 3500.0), (1, 3000.0, None, 3000.0)]


def test_the_pantry_sensitivity_runs_change_only_the_simulators_taps() -> None:
    """The robustness check (protocol change log, 1 Nov 2026): the first scenario is the
    harness as it runs; the others change what the simulator means by "wasted" or how often
    it taps, never the twin, and restore the simulator afterwards."""
    import gharbasket_sim.pantry as pantry_sim
    from gharbasket_training.eval import pantry_grid

    catalog = from_seed_files()
    start = date(2026, 10, 5)
    plain = pantry_baseline.run_pantry(7, catalog, weeks=3, start=start, adoption=0.02)
    lines: list[str] = []
    results = pantry_grid.sensitivity([7], catalog, adoption=0.02, weeks=3, out=lines.append)
    assert list(results) == list(pantry_grid.SCENARIOS) and len(lines) == 2 + len(results)
    first = next(iter(results.values()))
    assert first["mae"] == pytest.approx(plain.twin) and len(first["coverage"]) == 2
    assert pantry_sim.TAP_WASTED_SHARE == 0.25 and pantry_sim.consume.__module__ == (
        "gharbasket_sim.pantry"
    )


def test_the_pantry_grid_picks_sigma_by_coverage_and_the_weight_by_mae() -> None:
    """The 1 Nov grid on seeds 23/29/31 (MAE after 4 weeks, coverage after 4 weeks): the rule
    stated first (lowest MAE, coverage breaking ties) picks 0.05 / 0.125, whose interval is far
    too narrow; the corrected rule takes sigma_w from coverage and the weight from MAE."""
    from gharbasket_training.eval import pantry_grid

    cells = {
        (0.05, 0.125): (0.136, 0.557),
        (0.05, 0.25): (0.140, 0.477),
        (0.1, 0.125): (0.143, 0.854),
        (0.1, 0.25): (0.137, 0.801),
        (0.1, 0.5): (0.139, 0.718),
        (0.15, 1.0): (0.143, 0.835),
        (0.2, 2.0): (0.152, 0.893),
    }
    rows = [(mae, abs(cov - 0.8), sigma, w) for (sigma, w), (mae, cov) in cells.items()]
    first, chosen = pantry_grid.select(rows)
    assert first[2:] == (0.05, 0.125) and chosen[2:] == (0.1, 0.25)
