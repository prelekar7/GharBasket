"""The evaluation harness: the smoke run (what CI checks), the statistics and the report."""

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    assert cli.main(["--smoke", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "greedy" in out and "pantry" in out and "report:" in out
    assert "pantry twin scope" in out and "pantry twin target" in out
    assert "forecast v1 " in out and "forecast v1 - v0 wape" in out  # RQ3, 11 weeks
    assert list(tmp_path.glob("*.html")) and list(tmp_path.glob("*.json"))


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

    priors = pantry_baseline.learned_priors(11, catalog, weeks=3, start=start, adoption=0.02)

    assert priors and {product for _, product in priors} <= {"milk", "curd"}
    assert all(p.prior_mean_per_ae > 0 for p in priors.values())
    with pytest.raises(ValueError, match="another seed"):
        pantry_baseline.run_pantry(
            11, catalog, weeks=3, start=start, adoption=0.02, priors=priors, learned_from=11
        )
    run = pantry_baseline.run_pantry(
        7, catalog, weeks=3, start=start, adoption=0.02, priors=priors, learned_from=11
    )
    assert run.learned_from == 11 and len(run.twin_learned) == 2
    assert all(0 < e < 1 for e in run.twin_learned)

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
        greedy_evenings=7 if lam is None else 0,
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


def test_a_high_lambda_spreads_a_hand_built_week_over_the_kitchens() -> None:
    from gharbasket_training.eval import assignment_sweep
    from gharbasket_training.eval.assignment_sweep import Week

    # Two kitchens: one beside the society, one 1.7 km away. Cost alone sends every order to the
    # near one; a high λ must hand some to the far one.
    society = (19.0300, 73.0690)
    evenings = [
        [
            {"id": f"o{d}-{i}", "lat": society[0], "lng": society[1], "portions": 2, "skills": []}
            for i in range(2)
        ]
        for d in range(7)
    ]
    kitchens = [
        {"id": "near", "lat": society[0], "lng": society[1], "skills": ["veg"], "rating": 4.5},
        {"id": "far", "lat": 19.0450, "lng": society[1], "skills": ["veg"], "rating": 4.5},
    ]
    week = Week(evenings, kitchens)

    greedy = assignment_sweep.run_week(week, None)
    cost_only = assignment_sweep.run_week(week, 0.0)
    fair = assignment_sweep.run_week(week, 2000.0)

    assert greedy.solved and cost_only.solved and fair.solved
    assert len(fair.evening_status) == 7 and fair.greedy_evenings == 0
    assert greedy.greedy_evenings == 7
    assert cost_only.gini == greedy.gini == 0.5 and cost_only.kitchens_used == 1
    assert fair.gini < cost_only.gini and fair.kitchens_used == 2
    assert fair.cost_paise_per_portion > cost_only.cost_paise_per_portion
    # The quality penalty is ₹2.50 a portion (half a star below 5); the rest is the rider.
    assert cost_only.cost_paise_per_portion - cost_only.rider_paise_per_portion == 250


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

    costly = target(seeds(1100.0))  # +10 % cost
    assert costly["holds"] is False and costly["lambda"] is None

    # λ = 0 is the cost-only plan: even a fair cost-only week does not meet the target.
    zero = target(seeds(1100.0, zero_gini=0.1))
    assert zero["per_lambda"]["0"]["holds"] is False and zero["holds"] is False

    few = target(seeds(1020.0, n=3))
    assert few["holds"] is None and few["lambda"] is None
    assert all(v["holds"] is None for v in few["per_lambda"].values())

    # A seed where one point did not solve is dropped for every λ, and named.
    runs = seeds(1020.0)
    runs[2] = _sweep_run(2, {None: (0.6, 1000.0)}, **{"700.0": "partial"})
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

    catalog = from_seed_files()
    run = assignment_sweep.run_sweep(7, catalog, adoption=0.02)

    assert run is not None and run.orders > 0 and run.portions >= run.orders
    assert run.evenings == 7  # Sunday's no-cook households and the weekday draws
    assert [p.fairness_lambda for p in run.points] == [None, *assignment_sweep.LAMBDAS]
    assert all(p.solved and p.evening_status == ["solved"] * 7 for p in run.points)
    assert all(p.greedy_evenings == 0 for p in run.points[1:])
    assert all(0 <= p.gini <= 1 and p.cost_paise_per_portion > 0 for p in run.points)
    by_lambda = {p.fairness_lambda: p for p in run.points}
    assert by_lambda[2000.0].gini < by_lambda[0.0].gini

    built = report.build([], [], args={"smoke": True}, sweep_runs=[run])
    assert built["seeds"] == [7] and set(built["assignment"]) >= {"greedy", "0", "2000"}
    prov = built["provenance"]["assignment"]
    assert prov["lambdas"] == list(assignment_sweep.LAMBDAS) and prov["ortools_version"]
    assert prov["model"] == "assignment-cpsat-v0" and len(prov["societies_sha256"]) == 64
    target = built["assignment_targets"]["gini_le_0_25_at_le_5pct_cost"]
    assert target["holds"] is None and target["seeds"] == [7] and target["dropped_seeds"] == []
    rows = report.metrics_rows(built)
    assert {r["model_name"] for r in rows} >= {"assignment_cpsat", "assignment_greedy"}
    extra = next(r for r in rows if r["metric"] == "extra_cost_lambda_2000")
    assert set(extra["details"]["per_seed"]) == {"7"} and extra["details"]["dropped_seeds"] == []
    assert "λ sweep" in report.to_html(built)
