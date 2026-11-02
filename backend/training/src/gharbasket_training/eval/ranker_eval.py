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

**The arms**, as named in the model card: `lambdarank` (the model), `repeat_last_week` and
`top_frequency` (the protocol's baselines). §4 also lists TIFU-KNN; it is not an arm: production
has no TIFU-KNN scorer (its neighbour items are part of the candidate set every arm ranks).
**Metrics** per seed, means over the holdout baskets: NDCG@10, Recall@10, Recall@|basket|, the
candidates' coverage of the final box and the number of baskets; SHAP (mean |SHAP| share per
feature over the holdout candidates) per seed, summarised over the seeds.

**The M-B verdict** (fixed before the first protocol run, §7 2026-11-02): over at least 5 seeds,
the per-seed paired NDCG@10 difference, model - repeat last week, has a 95 % t-CI **lower** bound
≥ 0.05 (the harness's paired-CI-bound convention, as M-C v2, M-E, M-F and M-G); whether the mean
alone clears 0.05 is reported beside it. Recall@|basket| is reported with no target (§7
2026-10-08); the model against top frequency is reported, not judged. No verdict below 5 seeds.
"""

import argparse
import hashlib
import json
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample
from gharbasket_sim.pantry import price_of, weekly_prices
from gharbasket_sim.weeks import PLANNERS, cached, simulate

from gharbasket.modules.orders import api as orders_api
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
MODEL = "lambdarank"
BASELINES = ("repeat_last_week", "top_frequency")
ARMS = (MODEL, *BASELINES)  # the model card's names; model_metrics adds the `ranker_` prefix
METRICS = ("ndcg_at_10", "recall_at_10", "recall_at_basket", "candidate_coverage", "baskets")
PAIRED = ("ndcg_at_10", "recall_at_10", "recall_at_basket")
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
    metrics: dict[str, dict[str, float]]  # arm → metric (the card's `metrics`)
    shap_share: dict[str, float] = field(default_factory=dict)  # feature → mean |SHAP| share
    model_sha256: str = ""  # the trained model's text: the same seed gives the same model

    def value(self, arm: str, metric: str) -> float:
        return float(self.metrics[arm][metric])


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
) -> tuple[dict[str, list[dict[str, Any]]], int]:
    """The rows `train_ranker` reads (`weekly_cycles`, `box_lines`, `edit_feedback`, `prices`),
    built from the simulated weeks of every household the seed samples, and the households."""
    cohorts = config.load()
    households = sample(seed, cohorts, catalog, adoption)
    by_name = {c.name: c for c in cohorts.cohorts}
    plan = cached(PLANNERS[planner])
    hub = str(SEED_HUB_ID)
    cycles: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    edits: list[dict[str, Any]] = []
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
    return out, len(households)


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
    """One seed: the simulated rows, then the weekly job's `fit` (train, leave-last-basket-out
    scores of the model and both baselines, SHAP)."""
    rows, households = tables(
        seed, catalog, weeks=weeks, start=start, adoption=adoption, shocks=shocks, planner=planner
    )
    (trained,) = train_ranker.fit(rows, f"eval-{seed}")  # one data source
    metrics = trained.card["metrics"]
    if not metrics:
        raise RuntimeError(f"seed {seed}: no household has two baskets ({weeks} week(s))")
    ex = trained.explanations
    baskets = ranker.baskets_from_rows(
        rows["box_lines"], rows["edit_feedback"], rows["weekly_cycles"]
    )
    return RankerRun(
        seed=seed,
        households=households,
        weeks=weeks,
        planner=planner,
        baskets=len(baskets),
        train_rows=int(trained.card["train_rows"]),
        holdout_rows=int(ex.get("rows", 0)),
        metrics={arm: dict(metrics[arm]) for arm in ARMS},
        shap_share=dict(ex.get("mean_abs_shap_share", {})),
        model_sha256=hashlib.sha256(trained.model.model_to_string().encode()).hexdigest(),
    )


def summarise(runs: list[RankerRun]) -> dict[str, dict[str, Estimate]]:
    """Arm → metric: mean ± 95 % t-CI over the seeds."""
    return {arm: {m: estimate([r.value(arm, m) for r in runs]) for m in METRICS} for arm in ARMS}


def gap(run: RankerRun, base: str, metric: str) -> float:
    """The model minus a baseline within a seed (both rank the same candidates)."""
    return run.value(MODEL, metric) - run.value(base, metric)


def paired(runs: list[RankerRun]) -> dict[str, dict[str, Estimate]]:
    """Baseline → metric: the per-seed paired difference, model - baseline, mean ± 95 % t-CI."""
    return {
        base: {m: estimate([gap(r, base, m) for r in runs]) for m in PAIRED} for base in BASELINES
    }


def shap(runs: list[RankerRun]) -> dict[str, Estimate]:
    """Feature → its mean |SHAP| share over the seeds, largest first."""
    out = {f: estimate([r.shap_share.get(f, 0.0) for r in runs]) for f in ranker.FEATURES}
    return dict(sorted(out.items(), key=lambda kv: -kv[1].mean))


def verdict(runs: list[RankerRun]) -> dict[str, Any]:
    """The M-B target (module docstring): the NDCG@10 gap to repeat last week, paired 95 % CI
    lower bound ≥ 0.05; no verdict (None) below `MIN_SEEDS` seeds. Recall@|basket| beside it."""
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
        "rule": f"over >= {MIN_SEEDS} seeds: NDCG@10 model - repeat last week, per-seed paired "
        f"95 % t-CI lower bound >= {NDCG_MARGIN}; Recall@|basket| reported, no target; "
        "the gap to top frequency reported, not judged",
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
        "explain_method": ranker.EXPLAIN_METHOD,
        "ranker_sha256": hashlib.sha256(Path(ranker.__file__).read_bytes()).hexdigest(),
    }


def section(runs: list[RankerRun]) -> dict[str, Any]:
    """The report's `ranker` section; empty without runs."""
    if not runs:
        return {}
    return {
        "settings": settings(runs),
        "arms": {a: {m: e.as_dict() for m, e in ms.items()} for a, ms in summarise(runs).items()},
        "paired": {b: {m: e.as_dict() for m, e in ms.items()} for b, ms in paired(runs).items()},
        "shap_share": {f: e.as_dict() for f, e in shap(runs).items()},
        "verdict": verdict(runs),
    }


def metrics_rows(report: dict[str, Any], details: dict[str, Any]) -> list[dict[str, Any]]:
    """`intel.model_metrics` rows under the weekly job's model names (`ranker_<arm>`, split
    `holdout`: leave-last-basket-out), each arm's metrics and the model's paired gaps."""
    sec = report.get("ranker") or {}
    runs = report.get("runs", {}).get("ranker", [])
    if not sec:
        return []

    def row(arm: str, metric: str, e: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_name": f"ranker_{arm}",
            "version": report["commit"],
            "metric": metric,
            "value": e["mean"],
            "split": "holdout",
            "data_source": DATA_SOURCE,
            "details": {**details, "eval_harness": True, "ci95": e["ci95"], **extra},
        }

    rows = [
        row(arm, m, e, {"per_seed": {str(r["seed"]): r["metrics"][arm][m] for r in runs}})
        for arm, ms in sec["arms"].items()
        for m, e in ms.items()
        if m != "baskets"
    ]
    for base, ms in sec["paired"].items():
        for m, e in ms.items():
            per_seed = {
                str(r["seed"]): round(r["metrics"][MODEL][m] - r["metrics"][base][m], 4)
                for r in runs
            }
            extra = {"per_seed": per_seed, "verdict": sec["verdict"]}
            rows.append(row(MODEL, f"{m}_vs_{base}", e, extra))
    return rows


def html(report: dict[str, Any]) -> str:
    """The report's M-B section (empty without ranker runs)."""
    from gharbasket_training.eval.report import _cell, _table, _verdict

    sec = report.get("ranker")
    if not sec:
        return ""
    arms, pairs, v = sec["arms"], sec["paired"], sec["verdict"]
    table = _table(
        "M-B — next-basket ranker, leave-last-basket-out on simulated boxes (train_ranker's own "
        "code path): per seed, means over the holdout baskets, then mean ± 95 % t-CI",
        ["metric", *ARMS, *(f"{MODEL} - {b} (paired)" for b in BASELINES)],
        [
            [
                m,
                *(_cell(arms[a][m]) for a in ARMS),
                *(_cell(pairs[b][m]) if m in pairs[b] else "—" for b in BASELINES),
            ]
            for m in METRICS
        ],
    )
    checks = _table(
        "M-B hypothesis: NDCG@10 ≥ repeat last week + 0.05 (paired 95 % CI lower bound)",
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
        "hyperparameters and seed); the simulator drafts the box (M3 drafts it in production). "
        "Recall counts the basket's items among the candidates; candidate coverage is the share "
        "of the final box they contain (eval protocol §7, 2026-10-08 and 2026-11-02).</p>"
    )
    return table + checks + shares + note


def lines(report: dict[str, Any]) -> list[str]:
    """The CLI's summary lines for the ranker section."""
    sec = report.get("ranker") or {}
    if not sec:
        return []
    arms, v = sec["arms"], sec["verdict"]
    out = [
        f"ranker  {arm:16} NDCG@10 {ms['ndcg_at_10']['mean']:.3f} ± {ms['ndcg_at_10']['ci95']:.3f}"
        f"  Recall@|basket| {ms['recall_at_basket']['mean']:.3f}  Recall@10 "
        f"{ms['recall_at_10']['mean']:.3f}  coverage {ms['candidate_coverage']['mean']:.3f}"
        for arm, ms in arms.items()
    ]
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
