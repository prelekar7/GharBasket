"""The report: a self-contained HTML page and a JSON file in docs/eval, and the rows for
`intel.model_metrics` (pushed through `POST /v1/internal/intel/model-metrics` when the API is
configured)."""

import html
import json
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2

from gharbasket_training.eval import assignment_sweep
from gharbasket_training.eval.pantry_baseline import PantryRun
from gharbasket_training.eval.planner_ab import METRICS, PlannerRun, against_target, summarise
from gharbasket_training.eval.stats import Estimate, estimate
from gharbasket_training.settings import Settings


def commit() -> str:
    try:
        return subprocess.check_output(  # noqa: S603
            [shutil.which("git") or "git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:  # not a checkout (the Actions runner without git history)
        return "unknown"


def build(
    planner_runs: list[PlannerRun],
    pantry_runs: list[PantryRun],
    *,
    args: dict[str, Any],
    sweep_runs: list[assignment_sweep.SweepRun] | None = None,
) -> dict[str, Any]:
    summary = summarise(planner_runs)
    sweep = assignment_sweep.summarise(sweep_runs or [])
    weeks = max((len(r.cold_start) for r in pantry_runs), default=0)
    curve = [estimate([r.cold_start[k] for r in pantry_runs]) for k in range(weeks)]
    twin = [estimate([r.twin[k] for r in pantry_runs]) for k in range(weeks)]
    rule = estimate([r.rule_table_mae for r in pantry_runs])
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit(),
        "args": args,
        "seeds": sorted({r.seed for r in planner_runs} | {r.seed for r in pantry_runs}),
        "planner": {
            p: {m: e.as_dict() for m, e in metrics.items()} for p, metrics in summary.items()
        },
        "planner_targets": against_target(summary),
        "pantry": {
            "rule_table_mae": rule.as_dict(),
            "cold_start_mae": [e.as_dict() for e in curve],
            "twin_mae": [e.as_dict() for e in twin],
            # The twin's target: after four observed weeks (predicting the fifth).
            "target_mae_le_15pct_after_4_weeks": {
                "value": twin[3].mean if len(twin) >= 4 else None,
                "holds": len(twin) >= 4 and twin[3].mean <= 0.15,
            },
        },
        "assignment": {
            key: {m: e.as_dict() for m, e in metrics.items()} for key, metrics in sweep.items()
        },
        "assignment_targets": assignment_sweep.against_target(sweep),
        "runs": {
            "planner": [asdict(r) for r in planner_runs],
            "pantry": [asdict(r) for r in pantry_runs],
            "assignment": [asdict(r) for r in (sweep_runs or [])],
        },
    }


def metrics_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    details = {"seeds": report["seeds"], "commit": report["commit"], **report["args"]}
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
                    "details": {**details, "ci95": e["ci95"]},
                }
            )
    rows.append(
        {
            "model_name": "pantry_rule_table",
            "version": report["commit"],
            "metric": "mae_pct",
            "value": report["pantry"]["rule_table_mae"]["mean"],
            "split": "holdout",
            "data_source": "synthetic",
            "details": details,
        }
    )
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
                    "details": {**details, "ci95": e["ci95"]},
                }
            )
    for k, e in enumerate(report["pantry"]["twin_mae"], start=1):
        rows.append(
            {
                "model_name": "pantry_twin_v0",
                "version": report["commit"],
                "metric": f"mae_pct_week_{k}",
                "value": e["mean"],
                "split": "holdout",
                "data_source": "synthetic",
                "details": details,
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
                "details": details,
            }
        )
    return rows


async def push(http: httpx2.AsyncClient, settings: Settings, rows: list[dict[str, Any]]) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the metrics")
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


def to_html(report: dict[str, Any]) -> str:
    planners = list(report["planner"])
    ab = _table(
        "RQ2 — meal planner A/B (per seed means, then across seeds)",
        ["metric", *planners],
        [[m, *(_cell(report["planner"][p][m]) for p in planners)] for m in METRICS],
    )
    targets = _table(
        "M-C targets",
        ["hypothesis", "value", "holds"],
        [
            [k, f"{v['value']:.3f}", "yes" if v["holds"] else "no"]
            for k, v in report["planner_targets"].items()
        ],
    )
    curve = report["pantry"]["cold_start_mae"]
    twin = report["pantry"]["twin_mae"]
    pantry = _table(
        "RQ1 — pantry: predicting next week's use of the daily-use items (MAE as a share)",
        ["observed weeks", "rule table", "running mean", "pantry twin v0"],
        [
            ["0", _cell(report["pantry"]["rule_table_mae"]), "—", "—"],
            *(
                [str(k), _cell(report["pantry"]["rule_table_mae"]), _cell(e), _cell(t)]
                for k, (e, t) in enumerate(zip(curve, twin, strict=True), start=1)
            ),
        ],
    )
    target = report["pantry"]["target_mae_le_15pct_after_4_weeks"]
    value = "n/a" if target["value"] is None else f"{target['value']:.3f}"
    verdict = "holds" if target["holds"] else "does not hold yet"
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
    sweep = report.get("assignment", {})
    sweep_table = (
        _table(
            "RQ4 — fair kitchen assignment: the λ sweep (Gini of kitchens' earnings against cost)",
            ["λ", "Gini", "₹ a portion", "km a portion"],
            [
                [
                    key,
                    _cell(m["gini"]),
                    _cell(m["cost_paise_per_portion"]),
                    _cell(m["km_per_portion"]),
                ]
                for key, m in sweep.items()
            ],
        )
        if sweep
        else ""
    )
    sweep_target = report.get("assignment_targets", {}).get("gini_le_0_25_at_le_5pct_cost")
    sweep_note = ""
    if sweep_target is not None:
        verdict_g = (
            f"holds from λ = {sweep_target['value']}"
            if sweep_target["holds"]
            else "does not hold yet"
        )
        sweep_note = (
            '<p class="note">M-G target (Gini ≤ 0.25 at ≤ +5 % cost against greedy, Gini '
            f"{sweep_target['greedy_gini']}): {verdict_g}</p>"
        )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>GharBasket evaluation {html.escape(report['commit'])}</title>"
        f"<style>{style}</style></head><body><h1>Evaluation report</h1>"
        f'<p class="note">{intro}</p>{ab}{targets}{pantry}'
        f'<p class="note">M-A target (MAE ≤ 15 % after 4 weeks): {value} → {verdict} '
        "(the twin, M10 F13, is scored here when it exists).</p>"
        f"{sweep_table}{sweep_note}</body></html>\n"
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
