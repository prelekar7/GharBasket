"""`python -m gharbasket_training.eval` (`just eval`): run the harness and write the report."""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import httpx2
from gharbasket_sim import config
from gharbasket_sim.catalog import from_seed_files
from gharbasket_sim.weeks import PLANNERS

from gharbasket_training.eval import assignment_sweep, pantry_baseline, planner_ab, report
from gharbasket_training.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_SEEDS = (7, 11, 13, 17, 19)  # the protocol's five


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
        "--smoke", action="store_true", help="seed 7, one week, 2 %% adoption, no CP-SAT (CI)"
    )
    args = parser.parse_args(argv)
    if args.smoke:
        args.seeds, args.weeks, args.adoption = [7], 1, 0.02
        args.planners = [p for p in args.planners if not p.startswith("cpsat")]

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
    pantry_runs = [
        pantry_baseline.run_pantry(
            seed, catalog, weeks=args.weeks, start=args.start, adoption=args.adoption
        )
        for seed in args.seeds
    ]
    sweep_runs = []
    if not args.no_assignment:
        sweep_runs = [
            run
            for seed in args.seeds
            if (run := assignment_sweep.run_sweep(seed, catalog, adoption=args.adoption))
        ]
    built = report.build(
        planner_runs,
        pantry_runs,
        sweep_runs=sweep_runs,
        args={
            "weeks": args.weeks,
            "start": args.start.isoformat(),
            "planners": args.planners,
            "adoption": args.adoption,
            "shocks": not args.no_shocks,
            "smoke": args.smoke,
        },
    )
    html_path, json_path = report.write(built, args.out)
    for planner, metrics in built["planner"].items():
        print(
            f"{planner:7} acceptance {metrics['acceptance']['mean']:.3f}  "
            f"perishables {metrics['distinct_perishables']['mean']:.1f}  "
            f"variety {metrics['variety']['mean']:.1f}  "
            f"violations {metrics['hard_rule_violations']['mean']:.2f}"
        )
    print(
        f"pantry  rule table MAE {built['pantry']['rule_table_mae']['mean']:.3f}; running mean "
        + ", ".join(
            f"w{k + 1} {e['mean']:.3f}" for k, e in enumerate(built["pantry"]["cold_start_mae"])
        )
    )
    for key, metrics in built.get("assignment", {}).items():
        print(
            f"assign  λ={key:6} gini {metrics['gini']['mean']:.3f}  "
            f"₹/portion {metrics['cost_paise_per_portion']['mean'] / 100:.2f}"
        )
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
