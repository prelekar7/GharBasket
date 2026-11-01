"""`python -m gharbasket_training.eval` (`just eval`): run the harness and write the report."""

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx2
from gharbasket_sim import config
from gharbasket_sim.catalog import from_seed_files
from gharbasket_sim.weeks import PLANNERS

from gharbasket_training.eval import (
    assignment_sweep,
    forecast_ab,
    pantry_baseline,
    planner_ab,
    report,
)
from gharbasket_training.eval import routing as routing_eval
from gharbasket_training.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_SEEDS = (7, 11, 13, 17, 19)  # the protocol's five


def learned_from(seed: int, override: int | None) -> int:
    """The seed whose households the reference arm's priors are learned on: never `seed`
    itself; by default the next of the protocol's five (19 → 7), else seed + 1."""
    if override is not None and override != seed:
        return override
    if seed in DEFAULT_SEEDS:
        return DEFAULT_SEEDS[(DEFAULT_SEEDS.index(seed) + 1) % len(DEFAULT_SEEDS)]
    return seed + 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GharBasket evaluation harness (eval protocol).")
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument(
        "--weeks", type=int, default=5
    )  # four observed weeks, then a fifth to predict
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 10, 5))
    parser.add_argument("--planners", nargs="+", choices=sorted(PLANNERS), default=sorted(PLANNERS))
    parser.add_argument("--adoption", type=float, help="share of flats (default: cohorts.json)")
    parser.add_argument("--no-shocks", action="store_true")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "eval")
    parser.add_argument("--push", action="store_true", help="store the rows through the API")
    parser.add_argument("--no-assignment", action="store_true", help="skip the λ sweep (RQ4)")
    parser.add_argument(
        "--assign-lambdas",
        type=float,
        nargs="+",
        default=list(assignment_sweep.LAMBDAS),
        help="the λ sweep's grid, paise a portion of imbalance (default: the protocol's)",
    )
    parser.add_argument(
        "--assign-weeks",
        type=int,
        default=assignment_sweep.WEEKS,
        help="consecutive weeks per seed; the last is scored (default: 4 fill the 28-day window)",
    )
    parser.add_argument("--assign-kitchens", type=int, default=assignment_sweep.KITCHENS)
    parser.add_argument(
        "--assign-preferred-share",
        type=float,
        default=assignment_sweep.PREFERRED_SHARE,
        help="the share of tiffins booked with a preferred cook (default: all, as in production)",
    )
    parser.add_argument(
        "--assign-preferences",
        choices=assignment_sweep.PREFERENCES,
        default=assignment_sweep.PREFERENCES[0],
        help="how a household picks the menu it books: a favourite per seed (sticky), one per "
        "order (nightly), or a favourite skewed by popularity (popular)",
    )
    parser.add_argument(
        "--learned-from",
        type=int,
        help="the seed the pantry reference arm learns its priors on (default: the next of the "
        "five; never the scored seed itself)",
    )
    parser.add_argument(
        "--no-learned", action="store_true", help="skip the pantry learned-priors reference arm"
    )
    parser.add_argument("--no-forecast", action="store_true", help="skip forecast v0 vs v1 (RQ3)")
    parser.add_argument("--forecast-weeks", type=int, default=forecast_ab.WEEKS)
    parser.add_argument(
        "--forecast-start", type=date.fromisoformat, default=forecast_ab.START
    )  # the festival calendar starts here
    parser.add_argument(
        "--ranker-explanations",
        type=Path,
        help="a basket ranker run's explanations.json (written next to its model.txt by "
        "train_ranker): adds the 'Ranker explanations' section to the report",
    )
    # M-F routing (eval/routing.py): production's plan() against nearest neighbour.
    parser.add_argument("--no-routing", action="store_true", help="skip the M-F routing arm")
    parser.add_argument(
        "--routing-sizes",
        type=int,
        nargs="+",
        default=list(routing_eval.SIZES),
        help="societies a morning (default: 50, the headline, and 100 and 200 for scale)",
    )
    parser.add_argument(
        "--routing-time-limit",
        type=float,
        default=routing_eval.TIME_LIMIT_S,
        help="seconds for both waves (default: what M7 sends, 20)",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="CI: seed 7, one week, 2 %% adoption, no CP-SAT planner arm; the λ sweep still runs "
        "(CP-SAT assignment, small) unless --no-assignment",
    )
    args = parser.parse_args(argv)
    if args.assign_weeks < 1 or args.assign_kitchens < 1:
        parser.error("--assign-weeks and --assign-kitchens must be at least 1")
    if not all(0 <= lam <= 10_000 for lam in args.assign_lambdas):
        parser.error("--assign-lambdas must be within 0 to 10000")
    if not 0 <= args.assign_preferred_share <= 1:
        parser.error("--assign-preferred-share must be within 0 to 1")
    sweep_config = assignment_sweep.SweepConfig(
        lambdas=tuple(sorted(set(args.assign_lambdas))),
        weeks=args.assign_weeks,
        kitchens=args.assign_kitchens,
        preferred_share=args.assign_preferred_share,
        preferences=args.assign_preferences,
    )
    if args.smoke:
        args.seeds, args.weeks, args.adoption = [7], 1, 0.02
        args.no_learned = True  # one week has no week to predict
        args.planners = [p for p in args.planners if not p.startswith("cpsat")]
        args.forecast_weeks = 11  # the fewest with three backtest origins
        args.routing_sizes, args.routing_time_limit = [30], 1.0
    routing_eval.check_args(parser, args.routing_sizes, args.routing_time_limit)

    catalog = from_seed_files()
    shocks = None if args.no_shocks else config.load_shocks()
    planner_runs = [
        planner_ab.run_planner(
            planner,
            seed,
            catalog,
            weeks=args.weeks,
            start=args.start,
            adoption=args.adoption,
            shocks=shocks,
        )
        for planner in args.planners
        for seed in args.seeds
    ]
    sources = {} if args.no_learned else {s: learned_from(s, args.learned_from) for s in args.seeds}
    priors = {
        source: pantry_baseline.learned_priors(
            source, catalog, weeks=args.weeks, start=args.start, adoption=args.adoption
        )
        for source in sorted(set(sources.values()))
    }
    pantry_runs = [
        pantry_baseline.run_pantry(
            seed,
            catalog,
            weeks=args.weeks,
            start=args.start,
            adoption=args.adoption,
            priors=priors.get(sources[seed]) if seed in sources else None,
            learned_from=sources.get(seed),
        )
        for seed in args.seeds
    ]
    sweep_runs = []
    if not args.no_assignment:
        sweep_runs = [
            run
            for seed in args.seeds
            if (
                run := assignment_sweep.run_sweep(
                    seed, catalog, adoption=args.adoption, cfg=sweep_config
                )
            )
        ]
    forecast_runs = []
    if not args.no_forecast:
        forecast_runs = [
            run
            for seed in args.seeds
            for run in forecast_ab.run_forecast(
                seed,
                catalog,
                weeks=args.forecast_weeks,
                start=args.forecast_start,
                adoption=args.adoption,
                shocks=shocks,
            )
        ]
    routing_runs = []
    if not args.no_routing:
        routing_runs = [
            routing_eval.run_routing(seed, size, time_limit_s=args.routing_time_limit)
            for size in sorted(set(args.routing_sizes))
            for seed in args.seeds
        ]
    built = report.build(
        planner_runs,
        pantry_runs,
        sweep_runs=sweep_runs,
        forecast_runs=forecast_runs,
        ranker_explanations=(
            json.loads(args.ranker_explanations.read_text()) if args.ranker_explanations else None
        ),
        args={
            "weeks": args.weeks,
            "start": args.start.isoformat(),
            "planners": args.planners,
            "adoption": args.adoption,
            "shocks": not args.no_shocks,
            "smoke": args.smoke,
            "no_assignment": args.no_assignment,
            "assign_lambdas": list(sweep_config.lambdas),
            "assign_weeks": sweep_config.weeks,
            "assign_kitchens": sweep_config.kitchens,
            "assign_preferred_share": sweep_config.preferred_share,
            "assign_preferences": sweep_config.preferences,
            "learned_from": sources,
            "forecast_weeks": None if args.no_forecast else args.forecast_weeks,
            "forecast_start": args.forecast_start.isoformat(),
            "routing_sizes": None if args.no_routing else sorted(set(args.routing_sizes)),
            "routing_time_limit": args.routing_time_limit,
        },
        routing_runs=routing_runs,
    )
    html_path, json_path = report.write(built, args.out)
    for planner, metrics in built["planner"].items():
        print(
            f"{planner:7} acceptance {metrics['acceptance']['mean']:.3f}  "
            f"perishables {metrics['distinct_perishables']['mean']:.1f}  "
            f"variety {metrics['variety']['mean']:.1f}  "
            f"violations {metrics['hard_rule_violations']['mean']:.2f}"
        )
    for arm, against in built["planner_paired"].items():
        print(
            f"{arm:7} acceptance vs "
            + ", ".join(
                f"{base} {d['acceptance']['mean']:+.3f} ± {d['acceptance']['ci95']:.3f}"
                for base, d in against.items()
            )
        )
    if "verdict" in built["planner_targets"]:
        holds = built["planner_targets"]["verdict"]["holds"]
        print(f"M-C on acceptance (>= 0.70, above greedy and random): {'holds' if holds else 'no'}")
    if "v2_verdict" in built["planner_targets"]:
        targets = built["planner_targets"]
        lift = targets.get("v2_acceptance_over_v1")
        holds = targets["v2_verdict"]["holds"]
        first = built["planner_paired_later"]["from_week"]
        if lift is None:
            verdict = f"no verdict: the run has no week >= {first}"
        elif holds is None:
            verdict = "no verdict: fewer than 5 paired seeds"
        else:
            verdict = "holds" if holds else "does not hold"
        print(
            "v2 vs v1 acceptance"
            + (f", weeks >= {first}: {lift['value']:+.3f} ± {lift['ci95']:.3f}" if lift else "")
            + f" ({verdict})"
        )
    print(
        f"pantry  rule table MAE {built['pantry']['rule_table_mae']['mean']:.3f}; running mean "
        + ", ".join(
            f"w{k + 1} {e['mean']:.3f}" for k, e in enumerate(built["pantry"]["cold_start_mae"])
        )
    )
    scope = built["pantry"]["twin_scope"]
    weeks = ", ".join(f"w{k + 1}" for k in range(len(scope["twin_mae"])))
    series = lambda key: " ".join(f"{e['mean']:.3f}" for e in scope[key])  # noqa: E731
    print(
        f"pantry twin scope (milk, curd: daily use; {weeks}): "
        f"rule table {scope['rule_table_mae']['mean']:.3f}; "
        f"running mean {series('running_mean_mae')}; twin {series('twin_mae')}; "
        f"twin 80 % coverage {series('twin_coverage_80')}"
        + (
            f"; reference: twin from learned priors {series('twin_learned_mae')}"
            if scope.get("twin_learned_mae")
            else ""
        )
    )
    target = built["pantry"]["target_mae_le_15pct_after_4_weeks"]
    value = "n/a" if target["value"] is None else f"{target['value']:.3f}"
    upper = target.get("ci95_upper")
    holds = "holds on the mean" + (f"; 95 % CI upper bound {upper:.3f}" if upper else "")
    verdict = {None: "no verdict: fewer than 5 weeks", True: holds}.get(
        target["holds"], "does not hold: keep PANTRY_TWIN_ENABLED off"
    )
    print(f"pantry twin target MAE <= 0.15 after 4 weeks: {value} ({verdict})")
    for key, metrics in built.get("assignment", {}).items():
        extra, gini = metrics.get("extra_cost"), metrics["gini"]
        print(
            f"assign  λ={key:6} gini {gini['mean']:.3f} ± {gini['ci95']:.3f}  "
            f"cost ₹/portion {metrics['cost_paise_per_portion']['mean'] / 100:.2f}"
            + (f"  vs greedy {extra['mean']:+.3f} ± {extra['ci95']:.3f}" if extra else "")
        )
    sweep_target = built.get("assignment_targets", {}).get("gini_le_0_25_at_le_5pct_cost")
    if sweep_target is not None:
        holds = sweep_target["holds"]
        if holds is None:
            verdict = "no verdict: fewer than 5 paired seeds"
        else:
            verdict = f"holds from λ = {sweep_target['lambda']:g}" if holds else "does not hold"
        dropped = sweep_target["dropped_seeds"]
        print(
            f"M-G Gini <= 0.25 at <= +5 % cost: {verdict}"
            + (f" (dropped seeds {dropped})" if dropped else "")
        )
        chosen = sweep_target["rule_lambda"]
        print(
            "M-G λ by the rule fixed before the sweep: "
            + ("none (no verdict)" if chosen is None else f"{chosen:g}")
            + f" ({sweep_target['lambda_rule']})"
        )
    forecast = built.get("forecast", {})
    for arm, metrics in forecast.get("arms", {}).items():
        print(
            f"forecast {arm:8} "
            + "  ".join(f"{m} {e['mean']:.3f}" for m, e in metrics.items() if e["n"])
        )
    for metric, e in forecast.get("v1_minus_v0", {}).items():
        if metric in ("wape", "newsvendor_cost") and e["n"]:
            print(f"forecast v1 - v0 {metric} {e['mean']:+.3f} ± {e['ci95']:.3f}")
    for line in routing_eval.lines(built):
        print(line)
    print(f"report: {html_path} ({json_path.name})")
    if args.push:
        settings = Settings()

        async def go() -> int:
            async with httpx2.AsyncClient() as http:
                return await report.push(http, settings, report.metrics_rows(built))

        print(f"stored {asyncio.run(go())} metric rows through the API")
    return 0


if __name__ == "__main__":
    sys.exit(main())
