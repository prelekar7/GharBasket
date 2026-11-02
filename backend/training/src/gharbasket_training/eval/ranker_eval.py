"""M-B: the next-basket ranker against "repeat last week" and "top frequency" over the protocol's
seeds (eval protocol §4, §7 2026-11-02; handbook 08 M-B, M10 §3.7).

**The data** comes from the simulator in memory, as the other arms do (no database): per seed,
every household the seed samples lives `WEEKS` weeks from the harness's start under the rules
planner (production's fallback; the forecast arm's world uses it too) with the price shocks. The
weeks become the rows `train_ranker` reads from the snapshot tables, in the same shape:
`weekly_cycles` (a week the household ordered is `locked`, a skipped week `skipped`; delivery on
the seed hub's Tuesday, `tools.seed.SEED_DELIVERY_WEEKDAY`, cut-off at M4's `cutoff_for`, 21:00
IST the day before), `box_lines` (the approved box, with each line's `source`), `edit_feedback`
(the household's edits; `removed` and `swapped` mark a drafted line it took out) and `prices` (the
week's price list, valid from its Monday, as the households saw it). Then **the job's own code
path trains and scores it**: `train_ranker.fit` (`ranker.baskets_from_rows`, `ranker.build` with
leave-last-basket-out, `ranker.train` with the job's hyperparameters and seed, single-threaded and
deterministic, `ranker.evaluate` and `ranker.baseline_scores`, `ranker.explain`). Writing the
weeks through M3, M4 and M5 into a scratch database (`just sim --write-db`) would only add the
database round trip and differ in two ways the arm does not need: M3 drafts the box instead of
the simulator, and an unfunded week's order is cancelled (dropping that basket).

**`drafted` leaks the label** (review of PR #481). `baskets_from_rows` rebuilds a basket's draft
from the *final* box (its non-customer lines) plus the lines removed or swapped out, so every item
the household kept counts as drafted; and the simulator's `edit_box` puts every cooked dish's
ingredients back on the box as `ai` lines, so the rebuilt draft is about twice the true one. Two
views and three extra arms show what that is worth:

- **holdout** (leave-last-basket-out on `build`'s candidates, as the job scores it): the model
  (`lambdarank`), the protocol's baselines, `drafted_box` (score = drafted + 0.001 x frequency: the
  leak alone) and `lambdarank_no_drafted` (the same training with `drafted` held at 0, a constant
  LightGBM cannot split on, so the trees are those of a model without it);
- **serving** (what production sends `POST /basket/rank`: M3's `ranker_candidates` offers only the
  household's own past items, from its `MIN_RANKED_WEEKS`-th final box on, at most
  `MAX_CANDIDATES`, and never sets `drafted`): the same holdout baskets, candidates with frequency
  > 0, `drafted` = 0, scored by the trained models and both baselines.

Leak diagnostics per seed: P(final | drafted) and P(final | not drafted) over the holdout
candidates, the share of positives drafted, the true and the rebuilt draft size, and the fewest
positives in a holdout basket (NDCG@10's ceiling).

**The M-B verdict** (fixed before the first protocol run, §7 2026-11-02): over at least 5 seeds,
the per-seed paired NDCG@10 difference, model - repeat last week, on the holdout view, has a 95 %
t-CI **lower** bound ≥ 0.05 (the harness's paired-CI-bound convention, as M-C v2, M-E, M-F and
M-G); whether the mean alone clears 0.05 is reported beside it. Recall@|basket| is reported with
no target (§7 2026-10-08); everything else (top frequency, the extra arms, the serving view) is
reported, not judged. No verdict below 5 seeds.

`model_metrics` rows are named `eval_ranker_<arm>` (holdout) and `eval_ranker_serving_<arm>`, never
the weekly job's `ranker_<arm>`, so AI health's latest job metrics never show eval numbers.
"""

import argparse
import hashlib
import json
import sys
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample
from gharbasket_sim.pantry import price_of, weekly_prices
from gharbasket_sim.weeks import PLANNERS, cached, simulate

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions.service import MAX_CANDIDATES, MIN_RANKED_WEEKS
from gharbasket.tools.seed import SEED_DELIVERY_WEEKDAY, SEED_HUB_ID
from gharbasket_training import ranker
from gharbasket_training.eval.stats import Estimate, estimate
from gharbasket_training.jobs import train_ranker

WEEKS = 8  # `just sim`'s default, and the run behind the 28 Oct SHAP explanations
SMOKE_WEEKS = 3  # the fewest with a training week that has history, and a holdout week
PLANNER = "rules"
START = date(2026, 10, 5)  # the harness's start, a Monday
DATA_SOURCE = "synthetic"
MIN_SEEDS = 5  # the protocol's five; fewer → no verdict
NDCG_MARGIN = 0.05  # §4: NDCG@10 ≥ repeat-last-week + 0.05
MODEL = "lambdarank"  # the model card's names
NO_DRAFTED = "lambdarank_no_drafted"
DRAFTED_BOX = "drafted_box"
BASELINES = ("repeat_last_week", "top_frequency")  # the protocol's
ARMS = (MODEL, *BASELINES)  # the card's arms
MODELS = (MODEL, NO_DRAFTED)  # the arms paired against the others
VIEWS = {
    "holdout": (MODEL, NO_DRAFTED, DRAFTED_BOX, *BASELINES),
    "serving": (MODEL, NO_DRAFTED, *BASELINES),
}
METRICS = ("ndcg_at_10", "recall_at_10", "recall_at_basket", "candidate_coverage", "baskets")
PAIRED = ("ndcg_at_10", "recall_at_10", "recall_at_basket")
LEAK = (
    "p_final_if_drafted",
    "p_final_if_not_drafted",
    "positives_drafted",
    "true_draft_lines",
    "rebuilt_draft_lines",
    "min_positives",
)
TOL = 1e-9  # `ranker.evaluate` rounds to 4 decimals


@dataclass(frozen=True)
class RankerRun:
    seed: int
    households: int
    weeks: int
    planner: str
    baskets: int  # approved or locked baskets, every week
    train_rows: int
    holdout_rows: int
    metrics: dict[str, dict[str, float]]  # holdout view: arm → metric
    serving: dict[str, dict[str, float]] = field(default_factory=dict)  # serving view
    leak: dict[str, float] = field(default_factory=dict)  # `LEAK` diagnostics
    shap_share: dict[str, float] = field(default_factory=dict)  # feature → mean |SHAP| share
    model_sha256: str = ""  # the trained model's text: the same seed gives the same model

    def value(self, arm: str, metric: str, view: str = "holdout") -> float:
        return float((self.metrics if view == "holdout" else self.serving)[arm][metric])


def _cycle_id(seed: int, household: str, week: int) -> str:
    return f"{seed}:{household}:{week}"


def _delivery(monday: date) -> date:
    return monday + timedelta(days=(SEED_DELIVERY_WEEKDAY - monday.weekday()) % 7)


def tables(
    seed: int,
    catalog: Catalog,
    *,
    weeks: int = WEEKS,
    start: date = START,
    adoption: float | None = None,
    shocks: config.Shocks | None = None,
    planner: str = PLANNER,
) -> tuple[dict[str, list[dict[str, Any]]], int, list[int]]:
    """The rows `train_ranker` reads (`weekly_cycles`, `box_lines`, `edit_feedback`, `prices`),
    built from the simulated weeks of every household the seed samples; the households; and the
    true draft's line count of every ordered week (the simulator's, before the household's
    edits: what `baskets_from_rows` cannot see)."""
    cohorts = config.load()
    households = sample(seed, cohorts, catalog, adoption)
    by_name = {c.name: c for c in cohorts.cohorts}
    plan = cached(PLANNERS[planner])
    hub = str(SEED_HUB_ID)
    cycles: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
    drafts: list[int] = []
    for h in households:
        c = by_name[h.cohort]
        rows = simulate(
            seed,
            h,
            catalog,
            start,
            weeks,
            plan,
            shocks,
            price_sensitivity=c.price_sensitivity,
            feedback_rate=c.feedback_rate,
        )
        for week in rows.weeks:
            delivery = _delivery(week["monday"])
            cycles.append(
                {
                    "id": _cycle_id(seed, h.key, week["week"]),
                    "household_id": h.key,
                    "hub_id": hub,
                    "delivery_date": delivery,
                    "cutoff_at": orders_api.cutoff_for(delivery),
                    "status": "locked" if week["ordered"] else "skipped",
                    "data_source": DATA_SOURCE,
                }
            )
        lines += [
            {
                "cycle_id": _cycle_id(seed, h.key, line["week"]),
                "product_id": line["product"],
                "source": line["source"],
                "reason_code": line["reason"],
                "unit_price_paise": line["unit_price_paise"],
            }
            for line in rows.box_lines
        ]
        edits += [
            {
                "cycle_id": _cycle_id(seed, h.key, e["week"]),
                "product_id": e["product"],
                "action": e["action"],
            }
            for e in rows.edits
        ]
        drafts += [int(b["drafted_lines"]) for b in rows.boxes if b["ordered"] and b["lines"]]
    prices = [
        {
            "hub_id": hub,
            "valid_from": start + timedelta(weeks=w),
            "product_id": slug,
            "price_paise": price,
        }
        for w in range(weeks)
        for factor in [weekly_prices(catalog, shocks, w)]
        for slug, p in sorted(catalog.products.items())
        if (price := price_of(p, factor)) is not None
    ]
    out = {"weekly_cycles": cycles, "box_lines": lines, "edit_feedback": edits, "prices": prices}
    return out, len(households), drafts


def without_drafted(ds: ranker.Dataset) -> ranker.Dataset:
    """The same candidates with `drafted` = 0 (what serving sends, and the no-drafted model's
    training data: a constant column the trees cannot split on)."""
    rows = [{**row, "drafted": 0.0} for row in ds.rows]
    return ranker.Dataset(rows, list(ds.labels), list(ds.groups), list(ds.keys), ds.basket_sizes)


def drafted_box_scores(ds: ranker.Dataset) -> np.ndarray:
    """The leak alone: drafted items first, then the household's most frequent."""
    return np.array([row["drafted"] + 0.001 * row["frequency"] for row in ds.rows])


def serving_view(ds: ranker.Dataset, baskets: list[ranker.Basket]) -> ranker.Dataset:
    """The holdout baskets as production would rank them (M3's `ranker_candidates`): only the
    household's own past items (frequency > 0), only once it has `MIN_RANKED_WEEKS` earlier final
    boxes, at most `MAX_CANDIDATES` (the most often, then most recently, kept), `drafted` never
    set. A basket's size (for coverage) stays its final box's."""
    weeks: dict[str, list[date]] = defaultdict(list)
    for b in baskets:
        weeks[b.household].append(b.week)
    out, start = ranker.Dataset(), 0
    for size, basket_size in zip(ds.groups, ds.basket_sizes, strict=True):
        idx = range(start, start + size)
        start += size
        household, week, _ = ds.keys[idx[0]]
        if sum(w < week for w in weeks[household]) < MIN_RANKED_WEEKS:
            continue
        own = [i for i in idx if ds.rows[i]["frequency"] > 0]
        if len(own) > MAX_CANDIDATES:
            row = ds.rows
            kept = sorted(own, key=lambda i: (-row[i]["frequency"], row[i]["weeks_since"], i))
            own = sorted(kept[:MAX_CANDIDATES])
        if not own:
            continue
        out.rows += [{**ds.rows[i], "drafted": 0.0} for i in own]
        out.labels += [ds.labels[i] for i in own]
        out.keys += [ds.keys[i] for i in own]
        out.groups.append(len(own))
        out.basket_sizes.append(basket_size)
    return out


def leak(ds: ranker.Dataset, baskets: list[ranker.Basket], drafts: list[int]) -> dict[str, float]:
    """How much `drafted` says about the label on the holdout candidates, and why (`LEAK`)."""
    d = np.array([row["drafted"] for row in ds.rows])
    y = np.array(ds.labels)
    positives, start = [], 0
    for size in ds.groups:
        positives.append(int(y[start : start + size].sum()))
        start += size
    mean = lambda a: round(float(a.mean()), 4) if a.size else 0.0  # noqa: E731
    return {
        "p_final_if_drafted": mean(y[d == 1]),
        "p_final_if_not_drafted": mean(y[d == 0]),
        "positives_drafted": mean(d[y == 1]),
        "true_draft_lines": round(float(np.mean(drafts)), 2) if drafts else 0.0,
        "rebuilt_draft_lines": round(float(np.mean([len(b.drafted) for b in baskets])), 2),
        "min_positives": float(min(positives, default=0)),
    }


def run_ranker(
    seed: int,
    catalog: Catalog,
    *,
    weeks: int = WEEKS,
    start: date = START,
    adoption: float | None = None,
    shocks: config.Shocks | None = None,
    planner: str = PLANNER,
) -> RankerRun:
    """One seed: the simulated rows, the weekly job's `fit` (the model, its holdout scores
    against both baselines, SHAP), then the extra arms and the serving view on the same split."""
    rows, households, drafts = tables(
        seed, catalog, weeks=weeks, start=start, adoption=adoption, shocks=shocks, planner=planner
    )
    (trained,) = train_ranker.fit(rows, f"eval-{seed}")  # one data source
    baskets = ranker.baskets_from_rows(
        rows["box_lines"], rows["edit_feedback"], rows["weekly_cycles"]
    )
    # The split `fit` made (it does not return it): the same call on the same rows. The holdout
    # view is scored here, not read from the card, whose metrics may be the serving view's.
    train, test = ranker.build(baskets, holdout_last=True, prices=ranker.price_book(rows["prices"]))
    if not test.groups:
        raise RuntimeError(f"seed {seed}: no household has two baskets ({weeks} week(s))")
    blind = ranker.train(without_drafted(train))
    metrics = {MODEL: ranker.evaluate(test, ranker.predict(trained.model, test))} | {
        base: ranker.evaluate(test, ranker.baseline_scores(test, base)) for base in BASELINES
    }
    metrics[NO_DRAFTED] = ranker.evaluate(test, ranker.predict(blind, without_drafted(test)))
    metrics[DRAFTED_BOX] = ranker.evaluate(test, drafted_box_scores(test))
    serve = serving_view(test, baskets)
    serving: dict[str, dict[str, float]] = {}
    if serve.groups:
        serving = {
            MODEL: ranker.evaluate(serve, ranker.predict(trained.model, serve)),
            NO_DRAFTED: ranker.evaluate(serve, ranker.predict(blind, serve)),
        } | {
            base: ranker.evaluate(serve, ranker.baseline_scores(serve, base)) for base in BASELINES
        }
    ex = trained.explanations
    return RankerRun(
        seed=seed,
        households=households,
        weeks=weeks,
        planner=planner,
        baskets=len(baskets),
        train_rows=int(trained.card["train_rows"]),
        holdout_rows=int(ex.get("rows", 0)),
        metrics={arm: metrics[arm] for arm in VIEWS["holdout"]},
        serving=serving,
        leak=leak(test, baskets, drafts),
        shap_share=dict(ex.get("mean_abs_shap_share", {})),
        model_sha256=hashlib.sha256(trained.model.model_to_string().encode()).hexdigest(),
    )


def _scored(runs: list[RankerRun], view: str) -> list[RankerRun]:
    """The runs that have the view (a run too short for M3's minimum has no serving view)."""
    return [r for r in runs if view == "holdout" or r.serving]


def summarise(runs: list[RankerRun], view: str = "holdout") -> dict[str, dict[str, Estimate]]:
    """Arm → metric: mean ± 95 % t-CI over the seeds, for one view."""
    these = _scored(runs, view)
    return {
        arm: {m: estimate([r.value(arm, m, view) for r in these]) for m in METRICS}
        for arm in VIEWS[view]
    }


def gap(run: RankerRun, base: str, metric: str, arm: str = MODEL, view: str = "holdout") -> float:
    """An arm minus a baseline within a seed (both rank the same candidates)."""
    return run.value(arm, metric, view) - run.value(base, metric, view)


def paired(
    runs: list[RankerRun], view: str = "holdout"
) -> dict[str, dict[str, dict[str, Estimate]]]:
    """Model arm → other arm → metric: the per-seed paired difference, mean ± 95 % t-CI."""
    these = _scored(runs, view)
    return {
        arm: {
            base: {m: estimate([gap(r, base, m, arm, view) for r in these]) for m in PAIRED}
            for base in VIEWS[view]
            if base not in MODELS
        }
        for arm in MODELS
    }


def shap(runs: list[RankerRun]) -> dict[str, Estimate]:
    """Feature → its mean |SHAP| share over the seeds, largest first."""
    out = {f: estimate([r.shap_share.get(f, 0.0) for r in runs]) for f in ranker.FEATURES}
    return dict(sorted(out.items(), key=lambda kv: -kv[1].mean))


def leaks(runs: list[RankerRun]) -> dict[str, Estimate]:
    return {k: estimate([r.leak[k] for r in runs if k in r.leak]) for k in LEAK}


def verdict(runs: list[RankerRun]) -> dict[str, Any]:
    """The M-B target (module docstring): the NDCG@10 gap to repeat last week on the holdout
    view, paired 95 % CI lower bound ≥ 0.05; no verdict (None) below `MIN_SEEDS` seeds.
    Recall@|basket| beside it."""
    e = estimate([gap(r, BASELINES[0], "ndcg_at_10") for r in runs])
    lower = e.mean - e.ci
    top = estimate([gap(r, BASELINES[1], "ndcg_at_10") for r in runs])
    enough = e.n >= MIN_SEEDS
    return {
        "n": e.n,
        "seeds": sorted(r.seed for r in runs),
        "ndcg_gap_vs_repeat_last_week": e.as_dict(),
        "lower": round(lower, 4),
        "holds": lower >= NDCG_MARGIN - TOL if enough else None,
        "holds_on_mean": e.mean >= NDCG_MARGIN - TOL if enough else None,
        "ndcg_gap_vs_top_frequency": top.as_dict(),  # reported, not judged
        "top_frequency_lower": round(top.mean - top.ci, 4),
        "recall_at_basket": {  # reported, no target (§7 2026-10-08)
            arm: estimate([r.value(arm, "recall_at_basket") for r in runs]).as_dict()
            for arm in ARMS
        },
        "rule": f"over >= {MIN_SEEDS} seeds: NDCG@10 model - repeat last week (holdout view), "
        f"per-seed paired 95 % t-CI lower bound >= {NDCG_MARGIN}; Recall@|basket| reported, no "
        "target; top frequency, the extra arms and the serving view reported, not judged",
    }


def settings(runs: list[RankerRun]) -> dict[str, Any]:
    """What the arm's data and model came from, for the report's provenance."""
    first = runs[0]
    return {
        "weeks": first.weeks,
        "planner": first.planner,
        "data": "the simulator in memory, as train_ranker's snapshot rows (eval/ranker_eval.py)",
        "data_source": DATA_SOURCE,
        "delivery_weekday": SEED_DELIVERY_WEEKDAY,
        "cutoff": "orders.api.cutoff_for (21:00 IST the day before delivery)",
        "trainer": "jobs.train_ranker.fit (ranker.train defaults: 200 rounds, seed 7, 1 thread, "
        "deterministic)",
        "features": list(ranker.FEATURES),
        "k_neighbours": ranker.K_NEIGHBOURS,
        "neighbour_items": ranker.NEIGHBOUR_ITEMS,
        "serving": f"own past items (frequency > 0), from {MIN_RANKED_WEEKS} earlier final boxes, "
        f"at most {MAX_CANDIDATES}, drafted = 0 (subscriptions.service.ranker_candidates)",
        "explain_method": ranker.EXPLAIN_METHOD,
        "ranker_sha256": hashlib.sha256(Path(ranker.__file__).read_bytes()).hexdigest(),
    }


def section(runs: list[RankerRun]) -> dict[str, Any]:
    """The report's `ranker` section; empty without runs."""
    if not runs:
        return {}

    def view(name: str) -> dict[str, Any]:
        return {
            "n": len(_scored(runs, name)),
            "arms": {
                a: {m: e.as_dict() for m, e in ms.items()}
                for a, ms in summarise(runs, name).items()
            },
            "paired": {
                a: {b: {m: e.as_dict() for m, e in ms.items()} for b, ms in vs.items()}
                for a, vs in paired(runs, name).items()
            },
        }

    return {
        "settings": settings(runs),
        "views": {name: view(name) for name in VIEWS},
        "leak": {k: e.as_dict() for k, e in leaks(runs).items()},
        "shap_share": {f: e.as_dict() for f, e in shap(runs).items()},
        "verdict": verdict(runs),
    }


def model_name(arm: str, view: str) -> str:
    """Never the weekly job's `ranker_<arm>`: AI health reads those as the job's latest."""
    return f"eval_ranker_{arm}" if view == "holdout" else f"eval_ranker_{view}_{arm}"


def metrics_rows(report: dict[str, Any], details: dict[str, Any]) -> list[dict[str, Any]]:
    """`intel.model_metrics` rows (split `holdout`: leave-last-basket-out): each arm's metrics in
    each view, and the model arms' paired gaps (`<metric>_vs_<arm>`)."""
    sec = report.get("ranker") or {}
    runs = report.get("runs", {}).get("ranker", [])
    if not sec:
        return []

    def of(r: dict[str, Any], view: str) -> dict[str, dict[str, float]]:
        return r["metrics"] if view == "holdout" else r.get("serving") or {}

    def row(name: str, metric: str, e: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_name": name,
            "version": report["commit"],
            "metric": metric,
            "value": e["mean"],
            "split": "holdout",
            "data_source": DATA_SOURCE,
            "details": {**details, "eval_harness": True, "ci95": e["ci95"], **extra},
        }

    rows = []
    for view, v in sec["views"].items():
        scored = [r for r in runs if of(r, view)]
        for arm, ms in v["arms"].items():
            for m, e in ms.items():
                if m != "baskets" and e["n"]:
                    per_seed = {str(r["seed"]): of(r, view)[arm][m] for r in scored}
                    rows.append(row(model_name(arm, view), m, e, {"per_seed": per_seed}))
        for arm, vs in v["paired"].items():
            for base, ms in vs.items():
                for m, e in ms.items():
                    if not e["n"]:
                        continue
                    per_seed = {
                        str(r["seed"]): round(of(r, view)[arm][m] - of(r, view)[base][m], 4)
                        for r in scored
                    }
                    extra = {"per_seed": per_seed, "verdict": sec["verdict"]}
                    rows.append(row(model_name(arm, view), f"{m}_vs_{base}", e, extra))
    return rows


def html(report: dict[str, Any]) -> str:
    """The report's M-B section (empty without ranker runs)."""
    from gharbasket_training.eval.report import _cell, _table, _verdict

    sec = report.get("ranker")
    if not sec:
        return ""
    v = sec["verdict"]
    cell = lambda e: _cell(e) if e and e["n"] else "—"  # noqa: E731
    captions = {
        "holdout": "M-B — next-basket ranker, holdout view: leave-last-basket-out on the job's "
        "candidates (train_ranker's own code path; drafted is rebuilt from the final box, a "
        "label leak)",
        "serving": "M-B — serving view: the same holdout baskets with the candidates production "
        "sends (own past items, drafted never set; subscriptions.ranker_candidates)",
    }
    tables = []
    for name, view in sec["views"].items():
        arms = list(view["arms"])
        pairs = [(a, b) for a, vs in view["paired"].items() for b in vs]
        tables.append(
            _table(
                captions[name] + f": per seed, then mean ± 95 % t-CI over {view['n']} seed(s)",
                ["metric", *arms, *(f"{a} - {b}" for a, b in pairs)],
                [
                    [
                        m,
                        *(cell(view["arms"][a][m]) for a in arms),
                        *(cell(view["paired"][a][b].get(m)) for a, b in pairs),
                    ]
                    for m in METRICS
                ],
            )
        )
    checks = _table(
        "M-B hypothesis: NDCG@10 ≥ repeat last week + 0.05 (holdout view, paired 95 % CI lower "
        "bound)",
        ["check", "mean ± 95 % CI", "lower bound", "rule", "holds"],
        [
            [
                "NDCG@10 model - repeat last week",
                _cell(v["ndcg_gap_vs_repeat_last_week"]),
                f"{v['lower']:+.4f}",
                f"lower bound ≥ {NDCG_MARGIN} over ≥ {MIN_SEEDS} seeds",
                _verdict(v["holds"]),
            ],
            [
                "NDCG@10 model - top frequency",
                _cell(v["ndcg_gap_vs_top_frequency"]),
                f"{v['top_frequency_lower']:+.4f}",
                "reported, not judged",
                "not judged",
            ],
            *(
                [f"Recall@|basket|, {arm}", _cell(e), "—", "reported, no target", "not judged"]
                for arm, e in v["recall_at_basket"].items()
            ),
        ],
    )
    leak_table = _table(
        "M-B — the drafted leak on the holdout candidates (per seed, then over the seeds)",
        ["diagnostic", "mean ± 95 % CI"],
        [[k, cell(e)] for k, e in sec["leak"].items()],
    )
    shares = _table(
        "M-B — what the model leans on: mean |SHAP| share per feature over each seed's holdout "
        "candidates (TreeSHAP), then mean ± 95 % t-CI over the seeds",
        ["feature", "share of mean |SHAP|"],
        [[f, _cell(e)] for f, e in sec["shap_share"].items()],
    )
    if v["holds"] is None:
        said = f"no verdict: {v['n']} seed(s), the protocol needs {MIN_SEEDS}"
    else:
        said = ("holds" if v["holds"] else "does not hold") + (
            "; the mean alone " + ("clears" if v["holds_on_mean"] else "does not clear") + " 0.05"
        )
    st = sec["settings"]
    note = (
        f'<p class="note">M-B target (seeds {v["seeds"]}): {said}. Data: {st["weeks"]} simulated '
        f"weeks a seed, the {st['planner']} planner, every household the seed samples, written "
        "as the snapshot rows train_ranker reads and trained by its fit (same features, "
        "hyperparameters and seed). <strong>drafted is rebuilt from the final box: a label "
        "leak</strong>; the simulator drafting the box (every cooked dish's ingredients back on "
        "it) favours the model. The serving view is what the planner's learned preferences get. "
        "Once drafted leaves the model's features (PR #482), lambdarank and "
        "lambdarank_no_drafted coincide; both are kept for history. "
        "Recall counts the basket's items among the candidates; candidate coverage is the share "
        "of the final box they contain (eval protocol §7, 2026-10-08 and 2026-11-02).</p>"
    )
    return "".join(tables) + checks + leak_table + shares + note


def lines(report: dict[str, Any]) -> list[str]:
    """The CLI's summary lines for the ranker section."""
    sec = report.get("ranker") or {}
    if not sec:
        return []
    out = []
    for name, view in sec["views"].items():
        for arm, ms in view["arms"].items():
            if not ms["ndcg_at_10"]["n"]:
                continue
            out.append(
                f"ranker  {name:7} {arm:21} NDCG@10 {ms['ndcg_at_10']['mean']:.3f} ± "
                f"{ms['ndcg_at_10']['ci95']:.3f}  Recall@|basket| "
                f"{ms['recall_at_basket']['mean']:.3f} ± {ms['recall_at_basket']['ci95']:.3f}  "
                f"coverage {ms['candidate_coverage']['mean']:.3f}"
            )
    v = sec["verdict"]
    e, top = v["ndcg_gap_vs_repeat_last_week"], v["ndcg_gap_vs_top_frequency"]
    if v["holds"] is None:
        said = f"no verdict: {v['n']} seed(s)"
    else:
        said = ("holds" if v["holds"] else "does not hold") + (
            "; on the mean " + ("yes" if v["holds_on_mean"] else "no")
        )
    out.append(
        f"M-B NDCG@10 vs repeat last week {e['mean']:+.3f} ± {e['ci95']:.3f} (lower "
        f"{v['lower']:+.3f}, needs >= {NDCG_MARGIN}): {said}; vs top frequency "
        f"{top['mean']:+.3f} ± {top['ci95']:.3f} (reported)"
    )
    lk = sec["leak"]
    out.append(
        f"M-B drafted leak: P(final | drafted) {lk['p_final_if_drafted']['mean']:.3f}, "
        f"P(final | not) {lk['p_final_if_not_drafted']['mean']:.3f}; draft lines true "
        f"{lk['true_draft_lines']['mean']:.1f}, rebuilt {lk['rebuilt_draft_lines']['mean']:.1f}"
    )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m gharbasket_training.eval.ranker_eval`: the M-B arm alone, JSON and HTML in
    `--out` (the full report comes from `just eval`)."""
    from gharbasket_sim.catalog import from_seed_files

    from gharbasket_training.eval import report as rep

    parser = argparse.ArgumentParser(description="M-B ranker eval alone (eval protocol §4).")
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 11, 13, 17, 19])
    parser.add_argument("--weeks", type=int, default=WEEKS)
    parser.add_argument("--start", type=date.fromisoformat, default=START)
    parser.add_argument("--planner", choices=sorted(PLANNERS), default=PLANNER)
    parser.add_argument("--adoption", type=float, help="share of flats (default: cohorts.json)")
    parser.add_argument("--no-shocks", action="store_true")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    check_args(parser, args.weeks)
    started = time.perf_counter()
    catalog = from_seed_files()
    shocks = None if args.no_shocks else config.load_shocks()
    runs = [
        run_ranker(
            seed,
            catalog,
            weeks=args.weeks,
            start=args.start,
            adoption=args.adoption,
            shocks=shocks,
            planner=args.planner,
        )
        for seed in args.seeds
    ]
    built = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": rep.commit(),
        "args": {
            "seeds": args.seeds,
            "weeks": args.weeks,
            "start": args.start.isoformat(),
            "planner": args.planner,
            "adoption": args.adoption,
            "shocks": not args.no_shocks,
        },
        "provenance": rep.provenance(),
        "ranker": section(runs),
        "runs": {"ranker": [asdict(r) for r in runs]},
        "wall_seconds": round(time.perf_counter() - started, 1),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"ranker-{built['generated_at'][:10]}-{built['commit']}"
    (args.out / f"{stem}.json").write_text(json.dumps(built, indent=2, default=str) + "\n")
    page = f'<!doctype html><html lang="en"><meta charset="utf-8"><body>{html(built)}</body></html>'
    (args.out / f"{stem}.html").write_text(page + "\n")
    for line in lines(built):
        print(line)
    print(f"ranker: {args.out / stem}.json in {built['wall_seconds']} s")
    return 0


def check_args(parser: argparse.ArgumentParser, weeks: int) -> None:
    if weeks < SMOKE_WEEKS:
        parser.error(f"the ranker needs at least {SMOKE_WEEKS} weeks (history, then a holdout)")


if __name__ == "__main__":
    sys.exit(main())
