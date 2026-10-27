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

from gharbasket_training.eval import assignment_sweep, pantry_baseline, planner_ab, report
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
        "--learned-from",
        type=int,
        help="the seed the pantry reference arm learns its priors on (default: the next of the "
        "five; never the scored seed itself)",
    )
    parser.add_argument(
        "--no-learned", action="store_true", help="skip the pantry learned-priors reference arm"
    )
    parser.add_argument(
        "--ranker-explanations",
        type=Path,
        help="a basket ranker run's explanations.json (written next to its model.txt by "
        "train_ranker): adds the 'Ranker explanations' section to the report",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="CI: seed 7, one week, 2 %% adoption, no CP-SAT planner arm; the λ sweep still runs "
        "(CP-SAT assignment, small) unless --no-assignment",
    )
    args = parser.parse_args(argv)
    if args.smoke:
        args.seeds, args.weeks, args.adoption = [7], 1, 0.02
        args.no_learned = True  # one week has no week to predict
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
            if (run := assignment_sweep.run_sweep(seed, catalog, adoption=args.adoption))
        ]
    built = report.build(
        planner_runs,
        pantry_runs,
        sweep_runs=sweep_runs,
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
            "learned_from": sources,
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
    verdict = {None: "no verdict: fewer than 5 weeks", True: "holds"}.get(
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
