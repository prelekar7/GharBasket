"""The evaluation harness: the smoke run (what CI checks), the statistics and the report."""

import json
from datetime import date
from pathlib import Path

import httpx2
import pytest
from gharbasket_sim.catalog import from_seed_files
from gharbasket_training.eval import __main__ as cli
from gharbasket_training.eval import pantry_baseline, planner_ab, report
from gharbasket_training.eval.stats import estimate
from gharbasket_training.settings import Settings
from pydantic import SecretStr


def test_estimates_carry_a_t_interval() -> None:
    one = estimate([0.5])
    five = estimate([0.70, 0.72, 0.68, 0.71, 0.69])
    assert (one.mean, one.ci, one.n) == (0.5, 0.0, 1)
    assert five.n == 5 and abs(five.mean - 0.70) < 1e-9 and 0.015 < five.ci < 0.025
    assert str(five).startswith("0.700 ± 0.0")
    assert estimate([]).n == 0


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
    rows = report.metrics_rows(built)
    assert {r["model_name"] for r in rows} >= {"planner_greedy", "pantry_rule_table"}
    assert all(r["split"] == "holdout" and r["data_source"] == "synthetic" for r in rows)


def test_the_cli_smoke_writes_the_report(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--smoke", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "greedy" in out and "pantry" in out and "report:" in out
    assert list(tmp_path.glob("*.html")) and list(tmp_path.glob("*.json"))


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
