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
    assert all(0 < e < 1 for e in pantry.cold_start)

    built = report.build(runs, [pantry], args={"weeks": 1, "smoke": True})
    html_path, json_path = report.write(built, tmp_path)
    assert html_path.exists() and "meal planner A/B" in html_path.read_text()
    saved = json.loads(json_path.read_text())
    assert set(saved["planner"]) == {"rules", "greedy", "random"} and saved["seeds"] == [7]
    assert set(saved["planner_paired"]["rules"]) == {"greedy", "random"}
    assert saved["planner_targets"] == {}  # no CP-SAT arm in the smoke run
    # M-A's targets on the baseline are reference values: no verdict without a model arm.
    assert saved["pantry"]["target_mae_le_15pct_after_4_weeks"]["holds"] is None
    assert saved["pantry"]["cold_start_lt_4_weeks"]["holds"] is None
    assert set(saved["pantry"]["cold_start_lt_4_weeks"]["per_seed"]) == {"7"}
    prov = saved["provenance"]
    assert len(prov["cohorts_sha256"]) == 64 and "recipes.json" in prov["catalog_sha256"]
    assert prov["simulator_version"] and len(prov["simulator_source_sha256"]) == 64
    rows = report.metrics_rows(built)
    assert {r["model_name"] for r in rows} >= {"planner_greedy", "pantry_rule_table"}
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
