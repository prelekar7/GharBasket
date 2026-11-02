"""M-B: the ranker arm of the harness (eval/ranker_eval.py): the simulated weeks as the job's
snapshot rows, training through `train_ranker.fit` (deterministic per seed), the drafted-leak arms
and the serving view, the metrics, the paired verdict rule, the report section and rows (never
under the weekly job's model names), and the CLI flags."""

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, NoReturn

import numpy as np
import pytest
from gharbasket_sim.catalog import Catalog, from_seed_files
from gharbasket_training import ranker
from gharbasket_training.eval import __main__ as cli
from gharbasket_training.eval import ranker_eval, report
from gharbasket_training.eval.ranker_eval import (
    ARMS,
    BASELINES,
    DRAFTED_BOX,
    METRICS,
    MODEL,
    NO_DRAFTED,
    VIEWS,
    RankerRun,
)
from gharbasket_training.jobs import train_ranker

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions.service import MAX_CANDIDATES, MIN_RANKED_WEEKS
from gharbasket.tools.seed import SEED_HUB_ID

SMALL = {"weeks": 4, "adoption": 0.03}  # a handful of households: quick, the same code path


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return from_seed_files()


@pytest.fixture(scope="module")
def small(catalog: Catalog) -> RankerRun:
    return ranker_eval.run_ranker(7, catalog, **SMALL)  # pyright: ignore[reportArgumentType]


def test_the_simulated_weeks_become_the_jobs_snapshot_rows(catalog: Catalog) -> None:
    rows, households, drafts = ranker_eval.tables(7, catalog, **SMALL)  # pyright: ignore[reportArgumentType]
    cycles = rows["weekly_cycles"]
    assert len(cycles) == households * SMALL["weeks"]
    assert {c["status"] for c in cycles} <= {"locked", "skipped"}
    assert {c["hub_id"] for c in cycles} == {str(SEED_HUB_ID)}
    assert {c["data_source"] for c in cycles} == {"synthetic"}
    for c in cycles:
        assert c["delivery_date"].weekday() == 1  # the seed hub's Tuesday
        assert c["cutoff_at"] == orders_api.cutoff_for(c["delivery_date"])  # T-1 21:00 IST
    weeks = sorted({c["delivery_date"] for c in cycles})
    assert weeks[0] == date(2026, 10, 6) and len(weeks) == SMALL["weeks"]
    ids = {c["id"] for c in cycles}
    assert rows["box_lines"] and all(line["cycle_id"] in ids for line in rows["box_lines"])
    assert {line["source"] for line in rows["box_lines"]} <= {"ai", "rule", "customer"}
    assert {e["action"] for e in rows["edit_feedback"]} & {"removed", "swapped"}
    lists = sorted({p["valid_from"] for p in rows["prices"]})
    assert lists == [date(2026, 10, 5) + timedelta(weeks=w) for w in range(SMALL["weeks"])]
    # The job reads them: one basket per locked cycle that has a box.
    baskets = ranker.baskets_from_rows(
        rows["box_lines"], rows["edit_feedback"], rows["weekly_cycles"]
    )
    assert 0 < len(baskets) <= sum(c["status"] == "locked" for c in cycles)
    assert len(drafts) == len(baskets) and all(d > 0 for d in drafts)
    # The leak: the draft rebuilt from the final box is larger than the simulator's own draft.
    assert np.mean([len(b.drafted) for b in baskets]) > np.mean(drafts)
    again, _, _ = ranker_eval.tables(7, catalog, **SMALL)  # pyright: ignore[reportArgumentType]
    assert again == rows


def test_a_seed_trains_through_the_jobs_fit_deterministically(
    catalog: Catalog, small: RankerRun
) -> None:
    rows, _, _ = ranker_eval.tables(7, catalog, **SMALL)  # pyright: ignore[reportArgumentType]
    (job,) = train_ranker.fit(rows, "eval-7")
    # The card's holdout over the job's own candidates (its `metrics`, or once the card scores
    # under serving conditions, `metrics_build_candidates`).
    card = job.card.get("metrics_build_candidates", job.card["metrics"])
    assert {arm: small.metrics[arm] for arm in ARMS} == {arm: card[arm] for arm in ARMS}
    assert small.train_rows == job.card["train_rows"]
    assert small.shap_share == job.explanations["mean_abs_shap_share"]
    again = ranker_eval.run_ranker(7, catalog, **SMALL)  # pyright: ignore[reportArgumentType]
    assert again == small and len(small.model_sha256) == 64
    other = ranker_eval.run_ranker(11, catalog, **SMALL)  # pyright: ignore[reportArgumentType]
    assert other.model_sha256 != small.model_sha256


def test_every_arm_has_the_metrics_in_both_views(small: RankerRun) -> None:
    for view, arms in VIEWS.items():
        got = small.metrics if view == "holdout" else small.serving
        assert set(got) == set(arms)
        for arm in arms:
            assert set(got[arm]) == set(METRICS)
            for m in METRICS[:-1]:
                assert 0 <= small.value(arm, m, view) <= 1
        # Every arm of a view ranks the same candidates of the same baskets.
        assert len({small.value(a, "candidate_coverage", view) for a in arms}) == 1
        assert len({small.value(a, "baskets", view) for a in arms}) == 1
    assert small.households > 0 and small.baskets > 0 and small.holdout_rows > 0
    assert set(small.shap_share) == set(ranker.FEATURES)
    assert abs(sum(small.shap_share.values()) - 1) < 0.01
    # Serving offers only the household's own past items: they cover less of the final box.
    assert small.value(MODEL, "candidate_coverage", "serving") <= small.value(
        MODEL, "candidate_coverage"
    )
    assert set(small.leak) == set(ranker_eval.LEAK)
    assert small.leak["p_final_if_drafted"] > small.leak["p_final_if_not_drafted"]


def _ds(rows: list[dict[str, float]], labels: list[int], groups: list[int]) -> ranker.Dataset:
    keys, start = [], 0
    for g, (household, week) in zip(groups, [("h", date(2026, 10, 27))] * len(groups), strict=True):
        keys += [(household, week, f"p{i}") for i in range(start, start + g)]
        start += g
    return ranker.Dataset(rows, labels, groups, keys, [sum(labels)] * len(groups))


def _row(frequency: float, drafted: float = 1.0) -> dict[str, float]:
    return {f: 0.0 for f in ranker.FEATURES} | {
        "frequency": frequency,
        "drafted": drafted,
        "in_last_basket": 0.0,
    }


def _basket(household: str, week: date) -> ranker.Basket:
    return ranker.Basket(household, week, frozenset(), frozenset({"x"}), "synthetic")


def test_the_serving_view_is_what_m3_sends() -> None:
    week = date(2026, 10, 27)
    ds = _ds([_row(0.5), _row(0.0), _row(1.0, 0.0)], [1, 1, 0], [3])
    history = [_basket("h", week - timedelta(weeks=k)) for k in range(MIN_RANKED_WEEKS + 1)]
    serve = ranker_eval.serving_view(ds, history)
    # Own past items only (frequency > 0), drafted never set; the basket size is kept.
    assert serve.groups == [2] and serve.labels == [1, 0]
    assert all(row["drafted"] == 0.0 for row in serve.rows)
    assert serve.basket_sizes == ds.basket_sizes
    # Fewer than MIN_RANKED_WEEKS earlier final boxes: M3 sends nothing.
    assert ranker_eval.serving_view(ds, history[:MIN_RANKED_WEEKS]).groups == []
    # At most MAX_CANDIDATES: the most often kept first.
    many = _ds([_row(0.1)] * MAX_CANDIDATES + [_row(0.9)], [0] * MAX_CANDIDATES + [1], [501])
    capped = ranker_eval.serving_view(many, history)
    assert capped.groups == [MAX_CANDIDATES] and 1 in capped.labels


def test_the_leak_arms() -> None:
    ds = _ds([_row(0.2, 1.0), _row(0.9, 0.0)], [1, 0], [2])
    assert list(ranker_eval.drafted_box_scores(ds)) == pytest.approx([1.0002, 0.0009])
    blind = ranker_eval.without_drafted(ds)
    assert [r["drafted"] for r in blind.rows] == [0.0, 0.0] and ds.rows[0]["drafted"] == 1.0
    # A model trained with drafted held at 0 never uses it: its scores ignore the column.
    rows = [_row(f / 10, float(f % 2)) | {"popularity": f / 20} for f in range(10)] * 4
    train = _ds(rows, [int(f >= 5) for f in range(10)] * 4, [10] * 4)
    model = ranker.train(ranker_eval.without_drafted(train), rounds=20)
    flipped = ranker.Dataset(
        [{**r, "drafted": 1.0 - r["drafted"]} for r in train.rows],
        train.labels,
        train.groups,
        train.keys,
        train.basket_sizes,
    )
    assert np.allclose(ranker.predict(model, train), ranker.predict(model, flipped))
    importance = dict(zip(ranker.FEATURES, model.feature_importance(), strict=True))
    assert importance.get("drafted", 0) == 0  # (no column at all once it leaves FEATURES)


def _run(seed: int, ndcg_gap: float, *, repeat: float = 0.9) -> RankerRun:
    """A run whose model beats repeat last week on NDCG@10 by `ndcg_gap`."""
    base = dict.fromkeys(METRICS, 0.5) | {"baskets": 10.0}
    metrics = {
        MODEL: base | {"ndcg_at_10": repeat + ndcg_gap, "recall_at_basket": 0.9},
        NO_DRAFTED: base | {"ndcg_at_10": repeat, "recall_at_basket": 0.85},
        DRAFTED_BOX: base | {"ndcg_at_10": repeat, "recall_at_basket": 0.88},
        BASELINES[0]: base | {"ndcg_at_10": repeat, "recall_at_basket": 0.8},
        BASELINES[1]: base | {"ndcg_at_10": repeat + 0.01, "recall_at_basket": 0.85},
    }
    serving = {a: metrics[a] | {"recall_at_basket": 0.7} for a in VIEWS["serving"]}
    return RankerRun(
        seed=seed,
        households=10,
        weeks=8,
        planner="rules",
        baskets=60,
        train_rows=100,
        holdout_rows=20,
        metrics=metrics,
        serving=serving,
        leak=dict.fromkeys(ranker_eval.LEAK, 0.5),
        shap_share=dict.fromkeys(ranker.FEATURES, 1 / len(ranker.FEATURES)),
    )


def test_the_verdict_is_the_paired_lower_bound_over_five_seeds() -> None:
    seeds = (7, 11, 13, 17, 19)
    tight = [_run(s, 0.08 + 0.001 * i) for i, s in enumerate(seeds)]
    v = ranker_eval.verdict(tight)
    assert v["n"] == 5 and v["holds"] is True and v["holds_on_mean"] is True
    assert v["lower"] >= 0.05 and v["seeds"] == list(seeds)
    assert v["recall_at_basket"][MODEL]["mean"] == 0.9  # reported, no target
    # Four seeds: no verdict, however large the gap.
    assert ranker_eval.verdict(tight[:4])["holds"] is None
    assert ranker_eval.verdict(tight[:4])["holds_on_mean"] is None
    # The mean clears 0.05 but the interval does not: it does not hold.
    noisy = [_run(s, g) for s, g in zip(seeds, (0.0, 0.15, 0.02, 0.12, 0.01), strict=True)]
    v = ranker_eval.verdict(noisy)
    assert v["holds"] is False and v["holds_on_mean"] is True and v["lower"] < 0.05
    # Below the margin on the mean as well.
    low = ranker_eval.verdict([_run(s, 0.03) for s in seeds])
    assert low["holds"] is False and low["holds_on_mean"] is False
    # Exactly the margin, with no spread, holds (the metrics are rounded to 4 decimals).
    assert ranker_eval.verdict([_run(s, 0.05) for s in seeds])["holds"] is True
    # Top frequency is reported, not judged.
    assert ranker_eval.verdict(tight)["ndcg_gap_vs_top_frequency"]["mean"] == pytest.approx(
        0.072, abs=1e-3
    )


def test_the_paired_differences_are_model_arm_minus_the_others() -> None:
    runs = [_run(7, 0.1), _run(11, 0.2)]
    pairs = ranker_eval.paired(runs)
    assert set(pairs) == {MODEL, NO_DRAFTED}
    assert set(pairs[MODEL]) == {DRAFTED_BOX, *BASELINES}
    assert pairs[MODEL][BASELINES[0]]["ndcg_at_10"].mean == pytest.approx(0.15)
    assert pairs[MODEL][BASELINES[0]]["recall_at_basket"].mean == pytest.approx(0.1)
    assert pairs[MODEL][BASELINES[1]]["ndcg_at_10"].mean == pytest.approx(0.14)
    assert pairs[NO_DRAFTED][DRAFTED_BOX]["recall_at_basket"].mean == pytest.approx(-0.03)
    serving = ranker_eval.paired(runs, "serving")
    assert set(serving[MODEL]) == set(BASELINES)
    assert serving[MODEL][BASELINES[0]]["recall_at_basket"].mean == pytest.approx(0.0)
    # A run without a serving view (too short for M3's minimum) is left out of that view only.
    short = replace(_run(13, 0.1), serving={})
    assert ranker_eval.summarise([*runs, short], "serving")[MODEL]["ndcg_at_10"].n == 2
    assert ranker_eval.summarise([*runs, short])[MODEL]["ndcg_at_10"].n == 3


def test_the_report_section_rows_and_html(small: RankerRun) -> None:
    built = report.build([], [], args={}, ranker_runs=[small, replace(small, seed=11)])
    sec = built["ranker"]
    assert set(sec["views"]) == set(VIEWS)
    assert set(sec["views"]["holdout"]["arms"]) == set(VIEWS["holdout"])
    assert set(sec["views"]["serving"]["paired"][MODEL]) == set(BASELINES)
    assert sec["verdict"]["holds"] is None and sec["verdict"]["n"] == 2  # fewer than 5 seeds
    assert sec["settings"]["weeks"] == SMALL["weeks"] and sec["settings"]["planner"] == "rules"
    assert set(sec["leak"]) == set(ranker_eval.LEAK)
    assert built["seeds"] == [7, 11] and built["runs"]["ranker"][0]["seed"] == 7
    every = report.metrics_rows(built)
    # Never the weekly job's names: AI health reads `ranker_<arm>` as the job's latest metrics.
    assert not [r for r in every if r["model_name"].startswith("ranker_")]
    rows = [r for r in every if r["model_name"].startswith("eval_ranker_")]
    names = {(r["model_name"], r["metric"]) for r in rows}
    for arm in VIEWS["holdout"]:
        assert (f"eval_ranker_{arm}", "ndcg_at_10") in names
        assert (f"eval_ranker_{arm}", "recall_at_basket") in names
        assert (f"eval_ranker_{arm}", "baskets") not in names
    for arm in VIEWS["serving"]:
        assert (f"eval_ranker_serving_{arm}", "recall_at_basket") in names
    assert ("eval_ranker_lambdarank", "ndcg_at_10_vs_repeat_last_week") in names
    assert ("eval_ranker_lambdarank", "recall_at_basket_vs_drafted_box") in names
    assert ("eval_ranker_serving_lambdarank", "recall_at_basket_vs_top_frequency") in names
    assert all(r["split"] == "holdout" and r["data_source"] == "synthetic" for r in rows)
    assert all(len(r["metric"]) <= 40 and len(r["model_name"]) <= 60 for r in rows)
    assert all(r["details"]["eval_harness"] for r in rows)
    ndcg = next(
        r
        for r in rows
        if (r["model_name"], r["metric"]) == ("eval_ranker_lambdarank", "ndcg_at_10")
    )
    assert ndcg["details"]["per_seed"] == {"7": small.value(MODEL, "ndcg_at_10")} | {
        "11": small.value(MODEL, "ndcg_at_10")
    }
    page = report.to_html(built)
    part = page.split("M-B — next-basket ranker")[1]
    assert "M-B hypothesis" in part and "no verdict: 2 seed(s)" in part and "|SHAP|" in part
    assert "serving view" in part and "label leak" in part and "drafted leak" in part
    empty = report.build([], [], args={})
    assert empty["ranker"] == {} and "M-B" not in report.to_html(empty)
    assert not [r for r in report.metrics_rows(empty) if "ranker" in r["model_name"]]


def test_the_standalone_run_writes_json_and_html(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "ranker"
    flags = ["--seeds", "7", "--weeks", "4", "--adoption", "0.03", "--out", str(out)]
    assert ranker_eval.main(flags) == 0
    assert list(out.glob("ranker-*.json")) and list(out.glob("ranker-*.html"))
    printed = capsys.readouterr().out
    assert "ranker  holdout lambdarank" in printed and "no verdict: 1 seed(s)" in printed
    assert "ranker  serving lambdarank" in printed and "drafted leak" in printed
    with pytest.raises(SystemExit):
        ranker_eval.main(["--out", str(out), "--weeks", "2"])


class _Stop(Exception):
    pass


def _stop(built: dict[str, Any], out: Path) -> NoReturn:
    raise _Stop(built)


def test_the_cli_runs_the_ranker_unless_told_not_to(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    got: list[tuple[int, int, str, date]] = []

    def fake(seed: int, catalog: Catalog, **kw: Any) -> str:
        got.append((seed, kw["weeks"], kw["planner"], kw["start"]))
        return f"run {seed}"

    monkeypatch.setattr(ranker_eval, "run_ranker", fake)
    monkeypatch.setattr(cli.planner_ab, "run_planner", lambda *a, **k: None)
    monkeypatch.setattr(cli.pantry_baseline, "run_pantry", lambda *a, **k: None)
    monkeypatch.setattr(cli.report, "build", lambda *a, **k: k)
    monkeypatch.setattr(cli.report, "write", _stop)
    flags = ["--seeds", "7", "11", "--no-learned", "--no-forecast", "--no-assignment"]
    flags += ["--no-routing", "--out", str(tmp_path)]
    with pytest.raises(_Stop) as stop:
        cli.main(flags)
    start = date(2026, 10, 5)
    assert got == [(7, 8, "rules", start), (11, 8, "rules", start)]  # the defaults
    assert stop.value.args[0]["ranker_runs"] == ["run 7", "run 11"]
    assert stop.value.args[0]["args"]["ranker_weeks"] == 8

    got.clear()
    with pytest.raises(_Stop):
        cli.main([*flags, "--ranker-weeks", "5", "--ranker-planner", "greedy"])
    assert got == [(7, 5, "greedy", start), (11, 5, "greedy", start)]

    got.clear()
    with pytest.raises(_Stop):
        cli.main(
            ["--smoke", "--no-forecast", "--no-assignment", "--no-routing", "--out", str(tmp_path)]
        )
    assert got == [(7, ranker_eval.SMOKE_WEEKS, "rules", start)]

    got.clear()
    with pytest.raises(_Stop) as stop:
        cli.main([*flags, "--no-ranker"])
    assert got == [] and stop.value.args[0]["ranker_runs"] == []
    assert stop.value.args[0]["args"]["ranker_weeks"] is None

    with pytest.raises(SystemExit):
        cli.main([*flags, "--ranker-weeks", "2"])
