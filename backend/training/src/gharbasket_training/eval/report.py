"""The report: a self-contained HTML page and a JSON file in docs/eval, and the rows for
`intel.model_metrics` (pushed through `POST /v1/internal/intel/model-metrics` when the API is
configured)."""

import hashlib
import html
import importlib.metadata
import json
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import gharbasket_sim
import httpx2
from gharbasket_sim import config
from gharbasket_sim.catalog import SEED_FILES

from gharbasket_training.eval import assignment_sweep, forecast_ab
from gharbasket_training.eval.pantry_baseline import PantryRun
from gharbasket_training.eval.planner_ab import (
    FROM_WEEK,
    METRICS,
    PAIRED,
    V2,
    PlannerRun,
    against_target,
    paired,
    summarise,
)
from gharbasket_training.eval.stats import Estimate, estimate
from gharbasket_training.settings import Settings

SAMPLE_DECISIONS = 8  # decisions listed in the HTML (the JSON keeps them all)


def commit() -> str:
    try:
        return subprocess.check_output(  # noqa: S603
            [shutil.which("git") or "git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:  # not a checkout (the Actions runner without git history)
        return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_sha256(root: Path, paths: Iterable[Path]) -> str:
    """One digest over several files: each one's path (relative to root) and its contents."""
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def provenance() -> dict[str, Any]:
    """What the numbers came from (protocol §2): the simulator's version and source, the cohorts
    and shocks files and the seed catalog, each by sha256."""
    sim = Path(gharbasket_sim.__file__).resolve().parent
    return {
        "simulator_version": importlib.metadata.version("gharbasket-sim"),
        "simulator_source_sha256": _tree_sha256(sim, sim.rglob("*.py")),
        "cohorts_sha256": _sha256(config.COHORTS_FILE),
        "shocks_sha256": _sha256(config.SHOCKS_FILE),
        "catalog_sha256": {p.name: _sha256(p) for p in sorted(SEED_FILES.iterdir()) if p.is_file()},
    }


def _weeks_to_beat(rule: float, curve: list[float]) -> int | None:
    """Cold start: the fewest observed weeks after which the estimator's error is below the
    rule table's (None when it never is within the run)."""
    return next((k for k, e in enumerate(curve, start=1) if e < rule), None)


def build(
    planner_runs: list[PlannerRun],
    pantry_runs: list[PantryRun],
    *,
    args: dict[str, Any],
    sweep_runs: list[assignment_sweep.SweepRun] | None = None,
    forecast_runs: list[forecast_ab.ForecastRun] | None = None,
    ranker_explanations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = summarise(planner_runs)
    diffs = paired(planner_runs)
    later = paired(planner_runs, later=True, arms=(V2,))
    sweep_runs = sweep_runs or []
    forecast_runs = forecast_runs or []
    sweep = assignment_sweep.summarise(sweep_runs)
    weeks = max((len(r.cold_start) for r in pantry_runs), default=0)
    curve = [estimate([r.cold_start[k] for r in pantry_runs]) for k in range(weeks)]
    rule = estimate([r.rule_table_mae for r in pantry_runs])
    twin_rule = estimate([r.twin_rule_mae for r in pantry_runs])
    twin_mean = [estimate([r.twin_running_mean[k] for r in pantry_runs]) for k in range(weeks)]
    twin = [estimate([r.twin[k] for r in pantry_runs]) for k in range(weeks)]
    coverage = [estimate([r.twin_coverage[k] for r in pantry_runs]) for k in range(weeks)]
    # Reference only (no target): the twin from priors learned on another seed's households.
    with_learned = [r for r in pantry_runs if r.twin_learned]
    learned = (
        [estimate([r.twin_learned[k] for r in with_learned]) for k in range(weeks)]
        if with_learned and len(with_learned) == len(pantry_runs)
        else []
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit(),
        "args": args,
        "provenance": provenance()
        | ({"assignment": assignment_sweep.settings(sweep_runs[0].config)} if sweep_runs else {}),
        "seeds": sorted(
            {r.seed for r in planner_runs}
            | {r.seed for r in pantry_runs}
            | {r.seed for r in sweep_runs}
            | {r.seed for r in forecast_runs}
        ),
        "planner": {
            p: {m: e.as_dict() for m, e in metrics.items()} for p, metrics in summary.items()
        },
        # arm → baseline → metric: the per-seed paired difference, mean ± 95 % t-CI.
        "planner_paired": {
            arm: {
                base: {m: e.as_dict() for m, e in metrics.items()} for base, metrics in vs.items()
            }
            for arm, vs in diffs.items()
        },
        # v2 → v1 → metric over the weeks from FROM_WEEK on (v2 plans as v1 before them).
        "planner_paired_later": {
            "from_week": FROM_WEEK,
            "pairs": {
                arm: {
                    base: {m: e.as_dict() for m, e in metrics.items()}
                    for base, metrics in vs.items()
                }
                for arm, vs in later.items()
            },
        },
        "planner_targets": against_target(summary, diffs, later),
        "pantry": {
            "metric": "quantity MAE as a share of weekly quantity (sum |error| / sum actual per "
            "household item, zero weeks kept; mean over items)",
            "rule_table_mae": rule.as_dict(),
            "cold_start_mae": [e.as_dict() for e in curve],
            # The twin's scope: perishable daily-use items, their daily-use share (M10 §3.3).
            "twin_scope": {
                "rule_table_mae": twin_rule.as_dict(),
                "running_mean_mae": [e.as_dict() for e in twin_mean],
                "twin_mae": [e.as_dict() for e in twin],
                "twin_coverage_80": [e.as_dict() for e in coverage],
                # Reference arm, no target: priors learned on another seed (`learned_from`).
                "twin_learned_mae": [e.as_dict() for e in learned],
                "learned_from": {r.seed: r.learned_from for r in with_learned},
            },
            # M-A's MAE target, on the twin: after four observed weeks (predicting the fifth); no
            # verdict when the run is shorter. Until it holds, PANTRY_TWIN_ENABLED stays off and
            # M3 drafts from the rule table. The verdict is on the mean across seeds; the 95 %
            # CI's upper bound is reported next to it. The running mean (an oracle: it averages
            # the true use, which the app never sees) is shown for reference.
            "target_mae_le_15pct_after_4_weeks": {
                "estimator": "pantry_twin_v0 (twin scope)",
                "value": twin[3].mean if len(twin) >= 4 else None,
                "holds": twin[3].mean <= 0.15 if len(twin) >= 4 else None,
                "judged_on": "mean",
                "ci95_upper": twin[3].mean + twin[3].ci if len(twin) >= 4 else None,
                "reference_running_mean": curve[3].mean if len(curve) >= 4 else None,
            },
            # M-A's cold-start target, shown on the running-mean baseline for reference only.
            "cold_start_lt_4_weeks": {
                "estimator": "running_mean (reference only)",
                "value": _weeks_to_beat(rule.mean, [e.mean for e in curve]),
                "per_seed": {
                    r.seed: _weeks_to_beat(r.rule_table_mae, r.cold_start) for r in pantry_runs
                },
                "holds": None,
            },
        },
        # λ (or "greedy") → metric, over the seeds where every point solved (paired by seed);
        # `extra_cost` is the per-seed cost_λ / cost_greedy - 1.
        "assignment": {
            key: {m: e.as_dict() for m, e in metrics.items()} for key, metrics in sweep.items()
        },
        "assignment_targets": assignment_sweep.against_target(sweep_runs),
        "forecast": _forecast(forecast_runs),
        # The basket ranker's `explanations.json` (TreeSHAP, handbook 08 §8.4); None when the
        # harness was not given one.
        "ranker_explanations": ranker_explanations,
        "runs": {
            "planner": [asdict(r) for r in planner_runs],
            "pantry": [asdict(r) for r in pantry_runs],
            "assignment": [asdict(r) for r in sweep_runs],
            "forecast": [asdict(r) for r in forecast_runs],
        },
    }


def _forecast(runs: list[forecast_ab.ForecastRun]) -> dict[str, Any]:
    """RQ3: each arm's metrics over the seeds, v1 - v0 and the champion - each baseline paired
    by seed, the per-day split's own error, and the M-E verdict."""
    if not runs:
        return {}
    summary = forecast_ab.summarise(runs)
    splits = forecast_ab.per_seed(runs, lambda r: r.split_wape)
    # The per-seed values behind every estimate (a seed's hubs averaged, as the estimates are).
    seeds = lambda value: {str(s): v for s, v in forecast_ab.per_seed(runs, value).items()}  # noqa: E731
    return {
        "assumptions": forecast_ab.assumptions(),
        "arms": {arm: {m: e.as_dict() for m, e in ms.items()} for arm, ms in summary.items()},
        "v1_minus_v0": {m: e.as_dict() for m, e in forecast_ab.paired(runs).items()},
        # baseline arm → metric: the champion minus the baseline, per seed, mean ± 95 % t-CI.
        "champion_minus": {
            base: {m: e.as_dict() for m, e in ms.items()}
            for base, ms in forecast_ab.against_baselines(runs).items()
        },
        "per_seed": {
            arm: {m: seeds(forecast_ab.metric_of(arm, m)) for m in forecast_ab.METRICS}
            for arm in forecast_ab.ARMS
        },
        "champion_minus_per_seed": {
            base: {
                m: seeds(forecast_ab.difference("champion", base, m)) for m in forecast_ab.METRICS
            }
            for base in forecast_ab.BASELINES
        },
        "split_wape": estimate(list(splits.values())).as_dict(),
        "targets": forecast_ab.against_target(runs),
        "picks": [
            {
                "seed": r.seed,
                "hub": r.hub_id,
                "v0": r.v0_model,
                "champion": r.champion,
                "windows": r.windows,
            }
            for r in runs
        ],
    }


def metrics_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    details = {
        "seeds": report["seeds"],
        "commit": report["commit"],
        **report["args"],
        "provenance": report["provenance"],
    }
    runs = report["runs"]

    def per_seed(planner: str, metric: str) -> dict[str, float]:
        return {
            str(r["seed"]): r["metrics"][metric] for r in runs["planner"] if r["planner"] == planner
        }

    rows = []
    for planner, metrics in report["planner"].items():
        for metric, e in metrics.items():
            rows.append(
                {
                    "model_name": f"planner_{planner}",
                    "version": report["commit"],
                    "metric": metric,
                    "value": e["mean"],
                    "split": "holdout",
                    "data_source": "synthetic",
                    "details": {
                        **details,
                        "ci95": e["ci95"],
                        "per_seed": per_seed(planner, metric),
                    },
                }
            )
    rule = report["pantry"]["rule_table_mae"]
    rows.append(
        {
            "model_name": "pantry_rule_table",
            "version": report["commit"],
            "metric": "mae_pct",
            "value": rule["mean"],
            "split": "holdout",
            "data_source": "synthetic",
            "details": {
                **details,
                "ci95": rule["ci95"],
                "per_seed": {str(r["seed"]): r["rule_table_mae"] for r in runs["pantry"]},
            },
        }
    )
    target = report.get("assignment_targets", {}).get("gini_le_0_25_at_le_5pct_cost", {})
    paired_seeds = set(target.get("seeds", []))

    def sweep_per_seed(key: str, metric: str) -> dict[str, float]:
        out = {}
        for r in runs.get("assignment", []):
            if r["seed"] not in paired_seeds:
                continue
            points = {assignment_sweep.key(p["fairness_lambda"]): p for p in r["points"]}
            if metric == "extra_cost":
                base = points["greedy"]["cost_paise_per_portion"]
                out[str(r["seed"])] = points[key]["cost_paise_per_portion"] / base - 1
            else:
                out[str(r["seed"])] = points[key][metric]
        return out

    for key, metrics in report.get("assignment", {}).items():
        for metric, e in metrics.items():
            rows.append(
                {
                    "model_name": "assignment_greedy" if key == "greedy" else "assignment_cpsat",
                    "version": report["commit"],
                    "metric": metric if key == "greedy" else f"{metric}_lambda_{key}",
                    "value": e["mean"],
                    "split": "holdout",
                    "data_source": "synthetic",
                    "details": {
                        **details,
                        "ci95": e["ci95"],
                        "per_seed": sweep_per_seed(key, metric),
                        "dropped_seeds": target.get("dropped_seeds", []),
                    },
                }
            )
    scope = report["pantry"]["twin_scope"]
    for metric, series, field in (
        ("mae_pct", scope["twin_mae"], "twin"),
        ("coverage_80", scope["twin_coverage_80"], "twin_coverage"),
    ):
        for k, e in enumerate(series, start=1):
            rows.append(
                {
                    "model_name": "pantry_twin_v0",
                    "version": report["commit"],
                    "metric": f"{metric}_week_{k}",
                    "value": e["mean"],
                    "split": "holdout",
                    "data_source": "synthetic",
                    "details": {
                        **details,
                        "ci95": e["ci95"],
                        "per_seed": {str(r["seed"]): r[field][k - 1] for r in runs["pantry"]},
                    },
                }
            )
    for k, e in enumerate(scope.get("twin_learned_mae", []), start=1):
        rows.append(
            {
                "model_name": "pantry_twin_learned_priors",
                "version": report["commit"],
                "metric": f"mae_pct_week_{k}",
                "value": e["mean"],
                "split": "holdout",
                "data_source": "synthetic",
                "details": {
                    **details,
                    "reference_only": True,
                    "learned_from": scope.get("learned_from", {}),
                    "ci95": e["ci95"],
                    "per_seed": {str(r["seed"]): r["twin_learned"][k - 1] for r in runs["pantry"]},
                },
            }
        )
    forecast = report.get("forecast", {})
    # The per-seed values the estimates were built from (`forecast_ab.per_seed`).
    arm_seeds = forecast.get("per_seed", {})
    pair_seeds = forecast.get("champion_minus_per_seed", {})
    for arm, metrics in forecast.get("arms", {}).items():
        for metric, e in metrics.items():
            if e["n"]:
                rows.append(
                    {
                        "model_name": f"forecast_eval_{arm}",
                        "version": report["commit"],
                        "metric": metric,
                        "value": e["mean"],
                        "split": "backtest",
                        "data_source": "synthetic",
                        "details": {
                            **details,
                            "ci95": e["ci95"],
                            "picks": forecast["picks"],
                            "per_seed": arm_seeds.get(arm, {}).get(metric, {}),
                        },
                    }
                )
    # The champion against each baseline, paired by seed (the M-E target's gap, RQ3's cost).
    for base, metrics in forecast.get("champion_minus", {}).items():
        for metric, e in metrics.items():
            if e["n"]:
                rows.append(
                    {
                        "model_name": f"forecast_eval_champion_minus_{base}",
                        "version": report["commit"],
                        "metric": metric,
                        "value": e["mean"],
                        "split": "backtest",
                        "data_source": "synthetic",
                        "details": {
                            **details,
                            "ci95": e["ci95"],
                            "per_seed": pair_seeds.get(base, {}).get(metric, {}),
                            "m_e_target_holds": forecast["targets"]["target_holds"],
                            "rq3_cost_below_both": forecast["targets"]["cost_below_both"],
                        },
                    }
                )
    for k, e in enumerate(report["pantry"]["cold_start_mae"], start=1):
        rows.append(
            {
                "model_name": "pantry_running_mean",
                "version": report["commit"],
                "metric": f"mae_pct_week_{k}",
                "value": e["mean"],
                "split": "holdout",
                "data_source": "synthetic",
                "details": {
                    **details,
                    "ci95": e["ci95"],
                    "per_seed": {str(r["seed"]): r["cold_start"][k - 1] for r in runs["pantry"]},
                },
            }
        )
    return rows


async def push(http: httpx2.AsyncClient, settings: Settings, rows: list[dict[str, Any]]) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the metrics")
    if not rows:  # the endpoint takes at least one item; nothing to store is not an error
        return 0
    response = await http.post(
        f"{settings.api_url.rstrip('/')}/v1/internal/intel/model-metrics",
        json={"items": rows},
        headers={"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"},
        timeout=settings.http_timeout_s,
    )
    response.raise_for_status()
    return int(response.json()["accepted"])


def _cell(e: dict[str, Any]) -> str:
    return f"{e['mean']:.3f} ± {e['ci95']:.3f}" if e["n"] > 1 else f"{e['mean']:.3f}"


def _table(caption: str, header: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{html.escape(h)}</th>" for h in header)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><caption>{html.escape(caption)}</caption><tr>{head}</tr>{body}</table>"


def _verdict(holds: bool | None) -> str:
    return "reference only" if holds is None else ("yes" if holds else "no")


def to_html(report: dict[str, Any]) -> str:
    planners = list(report["planner"])
    ab = _table(
        "RQ2 — meal planner A/B (per seed means, then across seeds)",
        ["metric", *planners],
        [[m, *(_cell(report["planner"][p][m]) for p in planners)] for m in METRICS],
    )
    pairs = [(arm, base) for arm, vs in report["planner_paired"].items() for base in vs]
    paired_table = _table(
        "RQ2 — each arm against each baseline: the per-seed paired difference (arm - baseline; "
        "perishables_fewer and score_gain as shares of the baseline), mean ± 95 % t-CI",
        ["metric", *(f"{arm} - {base}" for arm, base in pairs)],
        [
            [m, *(_cell(report["planner_paired"][arm][base][m]) for arm, base in pairs)]
            for m in PAIRED
        ],
    )
    later = report.get("planner_paired_later", {})
    later_pairs = [(arm, base) for arm, vs in later.get("pairs", {}).items() for base in vs]
    later_table = (
        _table(
            f"RQ2 — planner v2 against v1 over weeks ≥ {later['from_week']} (week 0 is the first; "
            "before them v2 has no liking yet and plans exactly as v1): the per-seed paired "
            "difference, mean ± 95 % t-CI",
            ["metric", *(f"{arm} - {base}" for arm, base in later_pairs)],
            [
                [m, *(_cell(later["pairs"][arm][base][m]) for arm, base in later_pairs)]
                for m in PAIRED
            ],
        )
        if later_pairs
        else ""
    )
    targets = _table(
        "M-C hypotheses (CP-SAT; the primary metric, acceptance, first), then planner v2 "
        "against v1",
        ["hypothesis", "value", "rule", "holds"],
        [
            [
                k,
                _cell({"mean": v["value"], "ci95": v["ci95"], "n": v["n"]}) if "value" in v else "",
                v["rule"],
                _verdict(v["holds"]),
            ]
            for k, v in report["planner_targets"].items()
        ],
    )
    curve = report["pantry"]["cold_start_mae"]
    pantry = _table(
        "RQ1 baseline — next week's consumption of each daily-use item: quantity MAE as a share "
        "of the item's weekly quantity (Σ |error| / Σ actual per household item, zero weeks kept; "
        "the mean over items)",
        ["estimator", "MAE"],
        [
            ["family-size rule table (every week)", _cell(report["pantry"]["rule_table_mae"])],
            *(
                [f"running mean of {k} week(s), predicting week {k + 1}", _cell(e)]
                for k, e in enumerate(curve, start=1)
            ),
        ],
    )
    scope = report["pantry"]["twin_scope"]
    learned = scope.get("twin_learned_mae") or []
    twin = _table(
        "RQ1 — the pantry twin's scope: next week's daily use of milk and curd (MAE as a share; "
        "coverage of the 80 % interval)"
        + (
            "; reference arm, no target: the twin from cohort priors learned on another seed's "
            "households (seed → learned on: "
            + ", ".join(f"{a} → {b}" for a, b in scope.get("learned_from", {}).items())
            + ")"
            if learned
            else ""
        ),
        [
            "observed weeks",
            "rule table",
            "running mean (oracle: true use)",
            "pantry twin v0",
            "80 % coverage",
            *(["twin, learned priors (reference)"] if learned else []),
        ],
        [
            ["0", _cell(scope["rule_table_mae"]), "—", "—", "—", *(["—"] if learned else [])],
            *(
                [
                    str(k),
                    _cell(scope["rule_table_mae"]),
                    _cell(e),
                    _cell(t),
                    _cell(c),
                    *([_cell(learned[k - 1])] if learned else []),
                ]
                for k, (e, t, c) in enumerate(
                    zip(
                        scope["running_mean_mae"],
                        scope["twin_mae"],
                        scope["twin_coverage_80"],
                        strict=True,
                    ),
                    start=1,
                )
            ),
        ],
    )
    target = report["pantry"]["target_mae_le_15pct_after_4_weeks"]
    value = "n/a" if target["value"] is None else f"{target['value']:.3f}"
    upper = target.get("ci95_upper")
    holds = "holds on the mean" + (
        f" (the 95 % CI upper bound is {upper:.3f})" if upper is not None else ""
    )
    verdict = {None: "no verdict (fewer than 5 weeks)", True: holds}.get(
        target["holds"], "does not hold yet"
    )
    ref = target["reference_running_mean"]
    reference = "n/a" if ref is None else f"{ref:.3f}"
    cold = report["pantry"]["cold_start_lt_4_weeks"]
    weeks = "not within the run" if cold["value"] is None else f"{cold['value']} week(s)"
    prov = report["provenance"]
    catalog = ", ".join(f"{name} {digest[:12]}" for name, digest in prov["catalog_sha256"].items())
    style = (
        "body{font:15px/1.5 system-ui,sans-serif;max-width:60rem;margin:2rem auto;padding:0 1rem;"
        "color:#1b1b1b}table{border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #ccc;"
        "padding:.3rem .6rem;text-align:right}th:first-child,td:first-child{text-align:left}"
        "caption{text-align:left;font-weight:600;margin-bottom:.3rem}.note{color:#555;font-size:.9em}"
    )
    intro = (
        f"Commit {html.escape(report['commit'])}, {html.escape(report['generated_at'])}, seeds "
        f"{report['seeds']}, {html.escape(json.dumps(report['args']))}. Synthetic data only "
        "(ADR-0008); mean ± 95 % CI across seeds (docs/research/eval-protocol.md). Targets are "
        "hypotheses registered before any model was trained."
    )
    sweep_table = _sweep_html(report)
    source = (
        f"Simulator {html.escape(prov['simulator_version'])} (source sha256 "
        f"{prov['simulator_source_sha256'][:12]}), cohorts.json {prov['cohorts_sha256'][:12]}, "
        f"shocks.json {prov['shocks_sha256'][:12]}, catalog: {html.escape(catalog)} "
        "(full digests in the JSON)."
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>GharBasket evaluation {html.escape(report['commit'])}</title>"
        f"<style>{style}</style></head><body><h1>Evaluation report</h1>"
        f'<p class="note">{intro}</p><p class="note">{source}</p>'
        f"{ab}{paired_table}{later_table}{targets}{pantry}{twin}"
        f'<p class="note">M-A target (the twin\'s MAE ≤ 15 % after 4 weeks): {value} → {verdict}. '
        "The twin drafts boxes (PANTRY_TWIN_ENABLED) only once it holds. For reference, the "
        f"running mean's MAE after 4 weeks is {reference}, and it takes {weeks} to beat the rule "
        "table (cold-start target &lt; 4 weeks); the running mean is an oracle: it averages the "
        "true daily use, which the app never sees (the twin sees boxes and taps). The twin's "
        "tap meanings and its week-to-week spread match the simulator's own (protocol change "
        "log, 1 Nov 2026).</p>"
        f"{sweep_table}{_forecast_html(report)}{_ranker_html(report)}</body></html>\n"
    )


def _ranker_html(report: dict[str, Any]) -> str:
    """Ranker explanations: mean |SHAP| per feature, and the top features of a few decisions."""
    ex = report.get("ranker_explanations")
    if not ex or not ex.get("mean_abs_shap"):
        return ""
    share = ex.get("mean_abs_shap_share", {})
    importance = _table(
        f"Ranker explanations — global feature importance, mean |SHAP| over {ex['rows']} "
        f"{ex.get('data', '')} candidates ({ex['method']}; model {ex.get('version', '?')})",
        ["feature", "mean |SHAP|", "share"],
        [
            [f, f"{v:.4f}", f"{share.get(f, 0):.1%}"]
            for f, v in sorted(ex["mean_abs_shap"].items(), key=lambda kv: -kv[1])
        ],
    )
    decisions = ex.get("decisions", [])[:SAMPLE_DECISIONS]
    sample = _table(
        f"Ranker explanations — the top features of {len(decisions)} of the "
        f"{len(ex.get('decisions', []))} sampled decisions (kept = in the final box; "
        "contribution to the raw score, + raises the rank)",
        ["week", "product", "kept", "score", "top features (value → contribution)"],
        [
            [
                d["week"],
                d["product"],
                str(d["label"]),
                f"{d['score']:.3f}",
                "; ".join(
                    f"{t['feature']} {t['value']:g} → {t['contribution']:+.3f}"
                    for t in d["top_features"]
                ),
            ]
            for d in decisions
        ],
    )
    return importance + sample


def _rupees(e: dict[str, Any]) -> str:
    """A paise estimate, shown in rupees."""
    mean, ci = e["mean"] / 100, e["ci95"] / 100
    return f"{mean:.2f} ± {ci:.2f}" if e["n"] > 1 else f"{mean:.2f}"


def _sweep_html(report: dict[str, Any]) -> str:
    """RQ4: the λ sweep table and the M-G target, per λ."""
    sweep = report.get("assignment", {})
    target = report.get("assignment_targets", {}).get("gini_le_0_25_at_le_5pct_cost")
    if target is None:
        return ""
    table = (
        _table(
            "RQ4 — fair kitchen assignment: the λ sweep over consecutive simulated weeks (the "
            "preferred cook first, then the assignment on 28-day rolling earnings); the last "
            "week scored, per seed, then mean ± 95 % t-CI over the paired seeds",
            [
                "λ (paise a portion of imbalance)",
                "Gini of weekly earnings (eligible kitchens)",
                "Gini (every kitchen)",
                "cost a portion (distance + quality penalty, ₹)",
                "rider ₹ a portion",
                "km a portion",
                "share of portions to the preferred cook",
                "share of ordered portions left out (no kitchen in reach with room)",
                "cost vs greedy (per-seed ratio - 1)",
            ],
            [
                [
                    key,
                    _cell(m["gini"]),
                    _cell(m["gini_all"]),
                    _rupees(m["cost_paise_per_portion"]),
                    _rupees(m["rider_paise_per_portion"]),
                    _cell(m["km_per_portion"]),
                    _cell(m["preferred_share"]),
                    _cell(m["unassigned_share"]),
                    _cell(m["extra_cost"]) if "extra_cost" in m else "—",
                ]
                for key, m in sweep.items()
            ],
        )
        if sweep
        else ""
    )
    checks = _table(
        "M-G hypothesis: Gini ≤ 0.25 at ≤ +5 % cost against greedy (95 % CI upper bounds; "
        "λ = 0 is cost only and not eligible)",
        ["λ", "cost vs greedy, upper", "Gini, upper", "holds"],
        [
            [k, f"{v['extra_cost_upper']:+.3f}", f"{v['gini_upper']:.3f}", _verdict(v["holds"])]
            for k, v in target["per_lambda"].items()
        ],
    )
    if target["holds"] is None:
        verdict = f"no verdict: {target['n']} paired seed(s), the protocol needs 5"
    elif target["holds"]:
        verdict = f"holds from λ = {target['lambda']:g}"
    else:
        verdict = "does not hold at any λ"
    dropped, chosen = target["dropped_seeds"], target["rule_lambda"]
    note = (
        f'<p class="note">M-G target (greedy Gini {_cell(target["greedy_gini"])}): {verdict}. '
        f"Paired seeds {target['seeds']}"
        + (f"; dropped (CP-SAT fell back to greedy): {dropped}" if dropped else "")
        + ". λ by the rule fixed before the sweep: "
        + ("none (no verdict)" if chosen is None else f"{chosen:g}")
        + f" ({html.escape(target['lambda_rule'])}); production runs "
        "PARTNERS_FAIRNESS_LAMBDA, which a dated §7 entry may set apart from it.</p>"
    )
    return table + checks + note


def _forecast_html(report: dict[str, Any]) -> str:
    """RQ3: forecast v0, v1 and the champion on the synthetic uncommitted demand."""
    forecast = report.get("forecast")
    if not forecast:
        return ""
    arms, diff = forecast["arms"], forecast["v1_minus_v0"]
    against = forecast.get("champion_minus", {})
    cell = lambda e: _cell(e) if e and e["n"] else "—"  # noqa: E731
    table = _table(
        "RQ3 — forecast of the uncommitted demand (rolling origin, an origin a week, a 2-week "
        "horizon; q_tau at tau = C_u / (C_u + C_o)): per seed, then mean ± 95 % t-CI; lower is "
        "better except coverage, which should sit near tau. daily_*: the weekly q50 and q_tau "
        "split over the days by weekday shares as of the origin, against the daily orders",
        [
            "metric",
            "v0 (best StatsForecast)",
            "v1 (LightGBM)",
            "champion",
            "seasonal naive",
            "naive (buy last week)",
            "v1 - v0 (paired)",
            *(f"champion - {b.replace('_', ' ')} (paired)" for b in forecast_ab.BASELINES),
        ],
        [
            [
                m,
                *(cell(arms[a].get(m)) for a in forecast_ab.ARMS),
                cell(diff.get(m)),
                *(cell(against.get(b, {}).get(m)) for b in forecast_ab.BASELINES),
            ]
            for m in forecast_ab.METRICS
        ],
    )
    picks = ", ".join(f"{p['seed']}: {p['v0']} / {p['champion']}" for p in forecast["picks"])
    a = forecast["assumptions"]
    note = (
        '<p class="note">Synthetic uncommitted demand derived from the simulator for the harness '
        f"only: a shortfall becomes an add-on with probability {a['addon_take']} x price "
        f"multiplier^-{a['price_elasticity']} x (1 + {a['festival_lift_per_day']} per festival "
        f"day), a no-cook slot a tiffin with probability {a['tiffin_take']} "
        "(eval/forecast_ab.py). Newsvendor cost = C_o x waste + C_u x stock-out per unit of "
        f"demand (C_u {a['costs']['underage']}, C_o {a['costs']['overage']}). v0 and the "
        "champion are picked on the windows they are scored on (optimistic); v1 is fixed; the "
        "baselines get the same conformal q_tau as the models. The per-day split's own error "
        "(the actual weekly quantity split by the same shares, against the days): WAPE "
        f"{cell(forecast.get('split_wape'))}. Seed: v0 / champion: {html.escape(picks)}.</p>"
    )
    return table + _forecast_verdict_html(forecast) + note


def _forecast_verdict_html(forecast: dict[str, Any]) -> str:
    """The M-E target as registered and its verdict, RQ3's cost answer beside it (not part of
    the target), and the guardrails reported, not judged (eval protocol §4, §7 2026-11-02)."""
    target = forecast.get("targets")
    if not target:
        return ""
    judged = lambda v: "no verdict" if v is None else _verdict(v)  # noqa: E731
    table = _table(
        "M-E target (registered): the champion's WAPE ≤ 0.25 and ≥ 10 points better than "
        "seasonal naive, on 95 % CI bounds. Below it, not part of the target: RQ3's newsvendor "
        "cost against each baseline, and the guardrails (§7, 2026-11-02)",
        ["check", "mean ± 95 % CI", "bound", "rule", "holds"],
        [
            [k, _cell(c["value"]), f"{c['bound']:+.4f}", c["rule"], judged(c["holds"])]
            for k, c in target["checks"].items()
        ]
        + [
            [
                f"RQ3: newsvendor cost vs {base.replace('_', ' ')}",
                _cell(c["value"]),
                f"{c['bound']:+.4f}",
                c["rule"],
                "reported: " + ("lower" if c["below"] else "not lower")
                if c["below"] is not None
                else "no verdict",
            ]
            for base, c in target["newsvendor_cost"].items()
        ]
        + [
            [
                k,
                _cell(r["value"]),
                f"ceiling {r['ceiling']}" if "ceiling" in r else f"tau {r['tau']}",
                "registered ceiling, not judged" if "ceiling" in r else "should sit near tau",
                "not judged",
            ]
            for k, r in target["reported"].items()
        ],
    )
    holds, cheaper = target["target_holds"], target["cost_below_both"]
    if holds is None:
        verdict = f"no verdict: {target['n']} seed(s), the protocol needs {forecast_ab.MIN_SEEDS}"
    else:
        verdict = "holds" if holds else "does not hold"
    cost = (
        "no answer"
        if cheaper is None
        else ("below both baselines" if cheaper else "not below both baselines")
    )
    return table + (
        f'<p class="note">M-E target (seeds {target["seeds"]}): {verdict}. RQ3, newsvendor cost '
        f"(reported, not part of the target): {cost}. {html.escape(target['note'])}</p>"
    )


def write(report: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report['generated_at'][:10]}-{report['commit']}"
    html_path = out_dir / f"{stem}.html"
    json_path = out_dir / f"{stem}.json"
    html_path.write_text(to_html(report))
    json_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return html_path, json_path


__all__ = ["Estimate", "build", "metrics_rows", "push", "to_html", "write"]
