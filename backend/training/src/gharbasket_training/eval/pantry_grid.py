"""The pantry twin's two hyperparameters and its robustness to the simulator's assumptions
(M10 §3.3, eval protocol change log, 1 Nov 2026), through the RQ1 harness (`run_pantry`).

    uv run python -m gharbasket_training.eval.pantry_grid grid         # seeds 23 29 31
    uv run python -m gharbasket_training.eval.pantry_grid sensitivity  # the protocol's five

`grid` chooses the week-to-week spread (`gharbasket_ml.pantry.WEEK_SIGMA`) and the rule
prior's weight in weeks (`subscriptions.rules.RULE_PRIOR_WEEKS`) on seeds the protocol does not
report. The rule (corrected 1 Nov 2026, eval protocol change log): the week's spread sets the
interval's width, so it is chosen by coverage (the grid cell whose 80 % coverage after 4 weeks is
closest to 0.80 gives sigma_w); the prior weight is then the one with the lowest MAE after 4
weeks at that sigma_w. The rule stated first ("lowest MAE after 4 weeks, coverage breaking
ties") cannot see sigma_w; its pick is printed too, for the record. The simulations do not
depend on the twin, so each seed's households are simulated once.

`sensitivity` keeps the twin as served and changes what the simulator assumes about the taps:
- what "wasted" means (the simulator's `TAP_WASTED_SHARE`: 0.1 and 0.4 of the box left, while
  the twin keeps reading it as 0.25);
- whether a household taps independently of what happened (the simulator taps 40 % of boxes
  at random): an outcome-dependent rate, "ran out" tapped 70 % of the time, the others 30 %.
No number here was used to choose anything.
"""

import argparse
import random
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import date
from typing import Any

import gharbasket_ml.pantry as twin
import gharbasket_sim.pantry as pantry_sim
from gharbasket_sim.catalog import Catalog, from_seed_files

from gharbasket.modules.subscriptions import rules
from gharbasket_training.eval import pantry_baseline
from gharbasket_training.eval.stats import estimate

GRID_SEEDS = (23, 29, 31)
PROTOCOL_SEEDS = (7, 11, 13, 17, 19)
WEEK_SIGMAS = (0.05, 0.1, 0.15, 0.2)
PRIOR_WEEKS = (0.125, 0.25, 0.5, 1.0, 2.0)
START = date(2026, 10, 5)
WEEKS = 5

# name → (the simulator's wasted share, tap rate per outcome or None for its own 40 % at random)
SCENARIOS: dict[str, tuple[float, dict[str, float] | None]] = {
    "as simulated (wasted = 1/4 left, 40 % of boxes tapped at random)": (0.25, None),
    "the simulator's wasted = 1/10 left": (0.10, None),
    "the simulator's wasted = 4/10 left": (0.40, None),
    "taps depend on the outcome (ran out 70 %, others 30 %)": (
        0.25,
        {"ran_out": 0.7, "wasted": 0.3, "just_right": 0.3},
    ),
}


@contextmanager
def _twin(week_sigma: float, prior_weeks: float) -> Iterator[None]:
    saved = twin.WEEK_SIGMA, rules.RULE_PRIOR_WEEKS
    twin.WEEK_SIGMA, rules.RULE_PRIOR_WEEKS = week_sigma, prior_weeks
    try:
        yield
    finally:
        twin.WEEK_SIGMA, rules.RULE_PRIOR_WEEKS = saved


@contextmanager
def _simulator(wasted_share: float, tap_rates: dict[str, float] | None) -> Iterator[None]:
    """The simulator with another meaning of "wasted" and, with `tap_rates`, taps that depend
    on the outcome: every outcome is drawn as the simulator draws it (rate 1, so its own random
    stream is unchanged), then kept with its rate from a separate, seeded stream."""
    saved_share, saved_consume = pantry_sim.TAP_WASTED_SHARE, pantry_sim.consume
    thin = random.Random("gharbasket-pantry-sensitivity")  # noqa: S311 (simulation)

    def consume(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if tap_rates is None:
            return saved_consume(*args, **kwargs)
        rows = saved_consume(*args, **{**kwargs, "feedback_rate": 1.0})
        for row in rows:
            if row["signal"] is not None and thin.random() >= tap_rates[row["signal"]]:
                row["signal"] = None
        return rows

    pantry_sim.TAP_WASTED_SHARE, pantry_sim.consume = wasted_share, consume
    try:
        yield
    finally:
        pantry_sim.TAP_WASTED_SHARE, pantry_sim.consume = saved_share, saved_consume


def score(
    seeds: Sequence[int], catalog: Catalog, *, adoption: float | None = None, weeks: int = WEEKS
) -> dict[str, list[float]]:
    """The twin's MAE and 80 % coverage after 1..weeks-1 observed weeks, mean over `seeds`."""
    runs = [
        pantry_baseline.run_pantry(s, catalog, weeks=weeks, start=START, adoption=adoption)
        for s in seeds
    ]
    k = range(weeks - 1)
    return {
        "mae": [estimate([r.twin[i] for r in runs]).mean for i in k],
        "coverage": [estimate([r.twin_coverage[i] for r in runs]).mean for i in k],
    }


@contextmanager
def _simulated_once() -> Iterator[None]:
    saved = pantry_baseline.simulate
    seen: dict[tuple[Any, ...], Any] = {}

    def simulate(seed: int, h: Any, *args: Any, **kwargs: Any) -> Any:
        key = (seed, h.key, args[1], args[2])  # start, weeks
        if key not in seen:
            seen[key] = saved(seed, h, *args, **kwargs)
        return seen[key]

    pantry_baseline.simulate = simulate
    try:
        yield
    finally:
        pantry_baseline.simulate = saved


Row = tuple[
    float, float, float, float
]  # MAE after 4 weeks (3 decimals), |coverage - 0.8|, sigma_w, weight


def select(rows: list[Row]) -> tuple[Row, Row]:
    """(the first rule's pick, the corrected rule's pick). First: the lowest MAE, coverage
    breaking ties. Corrected: sigma_w from the cell whose coverage is closest to 0.80, then the
    weight with the lowest MAE at that sigma_w."""
    first = min(rows)
    sigma = min(rows, key=lambda r: (r[1], r[0]))[2]
    chosen = min((r for r in rows if r[2] == sigma), key=lambda r: (r[0], r[1]))
    return first, chosen


def grid(
    seeds: Sequence[int], catalog: Catalog, out: Callable[[str], None] = print
) -> tuple[float, float]:
    rows: list[Row] = []
    out("| sigma_w | rule prior weeks | MAE after 1-4 weeks | coverage 1-4 |")
    out("|---|---|---|---|")
    for sigma in WEEK_SIGMAS:
        for weeks in PRIOR_WEEKS:
            with _twin(sigma, weeks), _simulated_once():
                s = score(seeds, catalog)
            out(
                f"| {sigma:g} | {weeks:g} | {' '.join(f'{x:.3f}' for x in s['mae'])} | "
                f"{' '.join(f'{x:.3f}' for x in s['coverage'])} |"
            )
            rows.append((round(s["mae"][-1], 3), abs(s["coverage"][-1] - 0.8), sigma, weeks))
    first, chosen = select(rows)
    for name, pick in (
        ("the first rule (MAE, coverage breaking ties)", first),
        ("chosen (sigma_w by coverage, weight by MAE)", chosen),
    ):
        out(
            f"{name}: sigma_w = {pick[2]:g}, prior weeks = {pick[3]:g} (MAE after 4 weeks "
            f"{pick[0]:.3f}, coverage off 0.8 by {pick[1]:.3f})"
        )
    return chosen[2], chosen[3]


def sensitivity(
    seeds: Sequence[int],
    catalog: Catalog,
    *,
    adoption: float | None = None,
    weeks: int = WEEKS,
    out: Callable[[str], None] = print,
) -> dict[str, dict[str, list[float]]]:
    results = {}
    out("| scenario (the twin unchanged) | MAE after 1-4 weeks | coverage 1-4 |")
    out("|---|---|---|")
    for name, (share, rates) in SCENARIOS.items():
        with _simulator(share, rates):
            s = score(seeds, catalog, adoption=adoption, weeks=weeks)
        results[name] = s
        out(
            f"| {name} | {' '.join(f'{x:.3f}' for x in s['mae'])} | "
            f"{' '.join(f'{x:.3f}' for x in s['coverage'])} |"
        )
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The pantry twin's grid and sensitivity runs.")
    parser.add_argument("what", choices=["grid", "sensitivity"])
    parser.add_argument("--seeds", type=int, nargs="+")
    args = parser.parse_args(argv)
    catalog = from_seed_files()
    if args.what == "grid":
        grid(args.seeds or GRID_SEEDS, catalog)
    else:
        sensitivity(args.seeds or PROTOCOL_SEEDS, catalog)
    return 0


if __name__ == "__main__":
    sys.exit(main())
