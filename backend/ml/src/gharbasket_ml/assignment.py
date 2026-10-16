"""Fair kitchen assignment (M10 §3.5 `POST /assign/kitchens`, handbook 08 M-G).

Tomorrow's tiffin orders (portions at a society) go to home kitchens that can cook them: within
their capacity for the meal, with the menu skills the order needs, above the rating floor and
within reach. Only the nearest `max_kitchens_per_order` such kitchens are candidates for an order.

The CP-SAT model minimises travel distance plus a quality penalty, plus **λ times the earnings
imbalance** among the *eligible* kitchens (those that are a candidate for at least one order):
the largest deviation of any kitchen's rolling earnings (the last weeks plus tonight's
assignment) from the fair share, plus the mean absolute deviation, both counted in portions
(earnings ÷ `paise_per_portion`). The max is the linear proxy for the Gini coefficient; the mean
keeps the objective from going flat when one kitchen pins the max. So λ reads "paise of cost the
plan may spend per portion of imbalance"; λ = 0 is the cost-only plan, and the eval harness's λ
sweep draws the Pareto curve for the paper.

An order no kitchen can take is left out with a reason rather than failing the hub: every order
has a skip with a penalty per portion larger than any order's cost and fairness effect, so the
solver skips only what does not fit. `status` is then `partial`.

Greedy nearest kitchen is the baseline and the answer when the solver finds nothing in time.
"""

import time
from dataclasses import dataclass
from typing import Literal

from ortools.sat.python import cp_model

from gharbasket_ml.routing import haversine_m
from gharbasket_ml.schemas import (
    AssignmentOut,
    AssignRequest,
    AssignResponse,
    AssignSummary,
    KitchenEarningsOut,
    TiffinOrderIn,
    UnassignedOut,
)

MODEL = "assignment-cpsat-v0"
GREEDY = "greedy-v0"
PAISE_PER_PORTION_KM = 300  # the rider's cost of a kilometre, spread over a portion (₹3)
MIN_SOLVER_S = 0.05  # less than this left after building the model: greedy answers
OBJECTIVE_LIMIT = 2**60  # CP-SAT works in int64; larger instances go to greedy

Reason = Literal["below_rating_floor", "no_skills", "too_many_portions", "out_of_reach", "no_room"]


@dataclass(frozen=True)
class Candidate:
    order: int
    kitchen: int
    metres: int
    cost_paise: int  # distance and quality, per portion


def candidates(req: AssignRequest) -> list[Candidate]:
    """Every order's nearest `max_kitchens_per_order` kitchens that can cook it, in one pass."""
    reach = req.max_km * 1000
    kitchens = [
        (k, kitchen, set(kitchen.skills), (kitchen.lat, kitchen.lng))
        for k, kitchen in enumerate(req.kitchens)
        if kitchen.rating >= req.rating_floor and kitchen.max_portions > 0
    ]
    quality = {
        k: round((5.0 - kitchen.rating) * req.quality_paise_per_star)
        for k, kitchen, _, _ in kitchens
    }
    out = []
    for o, order in enumerate(req.orders):
        need = set(order.skills)
        here = (order.lat, order.lng)
        near = []
        for k, kitchen, skills, at in kitchens:
            if kitchen.max_portions < order.portions or not need <= skills:
                continue
            metres = haversine_m(here, at)
            if metres <= reach:
                near.append((metres, k))
        near.sort()
        for metres, k in near[: req.max_kitchens_per_order]:
            m = round(metres)
            out.append(Candidate(o, k, m, m * PAISE_PER_PORTION_KM // 1000 + quality[k]))
    return out


def why_not(order: TiffinOrderIn, req: AssignRequest) -> Reason:
    """Why an order has no candidate kitchen: the first filter that leaves none."""
    left = [k for k in req.kitchens if k.rating >= req.rating_floor]
    if not left:
        return "below_rating_floor"
    left = [k for k in left if set(order.skills) <= set(k.skills)]
    if not left:
        return "no_skills"
    left = [k for k in left if k.max_portions >= order.portions]
    if not left:
        return "too_many_portions"
    return "out_of_reach"


def greedy(req: AssignRequest, pairs: list[Candidate]) -> dict[int, int]:
    """Each order to its cheapest kitchen with room, biggest orders first; an order that fits
    nowhere is left out."""
    room = [k.max_portions for k in req.kitchens]
    by_order: dict[int, list[Candidate]] = {}
    for c in pairs:
        by_order.setdefault(c.order, []).append(c)
    chosen: dict[int, int] = {}
    for o in sorted(range(len(req.orders)), key=lambda o: (-req.orders[o].portions, o)):
        need = req.orders[o].portions
        fits = [c for c in by_order.get(o, []) if room[c.kitchen] >= need]
        if fits:
            best = min(fits, key=lambda c: (c.cost_paise, c.kitchen))
            chosen[o] = best.kitchen
            room[best.kitchen] -= need
    return chosen


def solve(
    req: AssignRequest,
    pairs: list[Candidate],
    *,
    started: float,
    deterministic: bool,
    hint: dict[int, int] | None = None,
) -> dict[int, int] | None:
    """The CP-SAT plan, or None when there is no time left, the numbers do not fit in int64, or
    the solver finds nothing. Building the model counts against `time_limit_s` (wall clock)."""
    orders, kitchens = req.orders, req.kitchens
    portions_of: dict[int, int] = {}  # per kitchen: the most it could be given
    most_cost: dict[int, int] = {}  # per order
    for c in pairs:
        portions_of[c.kitchen] = portions_of.get(c.kitchen, 0) + orders[c.order].portions
        most_cost[c.order] = max(most_cost.get(c.order, 0), c.cost_paise)
    eligible = sorted(portions_of)
    n = len(eligible)

    # Check the totals before building anything. Objective (integers) = scale·cost +
    # weight·(n·max deviation + Σ deviations) + the skips, i.e. 10n times (cost + λ·max + λ·mean)
    # with deviations in portions and λ in tenths of a paisa.
    fair = req.fairness_lambda > 0 and n > 1
    weight = round(10 * req.fairness_lambda) if fair else 0
    scale = 10 * n if fair else 1
    skip_weight = 2 * (scale * max(most_cost.values()) + weight * (n + 1)) + 1  # per portion
    bound = sum(orders[o].portions * (scale * c + skip_weight) for o, c in most_cost.items())
    before: dict[int, int] = {}
    upper: dict[int, int] = {}
    share = 0
    if fair:
        # Earnings in portions; the fair share assumes every order with a candidate is cooked.
        before = {k: round(kitchens[k].earnings_paise / req.paise_per_portion) for k in eligible}
        share = round((sum(before.values()) + sum(orders[o].portions for o in most_cost)) / n)
        for k in eligible:
            most = min(kitchens[k].max_portions, portions_of[k])
            upper[k] = max(abs(before[k] - share), abs(before[k] + most - share))
        bound += weight * 2 * n * max(upper.values())
    if bound > OBJECTIVE_LIMIT:
        return None

    # Group the variables by order and by kitchen in one pass.
    m = cp_model.CpModel()
    by_order: dict[int, list[tuple[cp_model.IntVar, Candidate]]] = {}
    by_kitchen: dict[int, list[tuple[cp_model.IntVar, int]]] = {}
    for c in pairs:
        v = m.new_bool_var("")
        by_order.setdefault(c.order, []).append((v, c))
        by_kitchen.setdefault(c.kitchen, []).append((v, orders[c.order].portions))

    cost_vars, cost_coefs, skip_vars, skip_coefs = [], [], [], []
    for o, mine in by_order.items():
        skip = m.new_bool_var("")
        m.add_exactly_one([v for v, _ in mine] + [skip])
        p = orders[o].portions
        for v, c in mine:
            cost_vars.append(v)
            cost_coefs.append(c.cost_paise * p)
        skip_vars.append(skip)
        skip_coefs.append(skip_weight * p)
        if hint is not None:
            m.add_hint(skip, o not in hint)
            for v, c in mine:
                m.add_hint(v, hint.get(o) == c.kitchen)

    objective = scale * cp_model.LinearExpr.weighted_sum(cost_vars, cost_coefs)
    objective += cp_model.LinearExpr.weighted_sum(skip_vars, skip_coefs)
    devs = []
    for k in eligible:
        mine_k = by_kitchen[k]
        load = cp_model.LinearExpr.weighted_sum([v for v, _ in mine_k], [p for _, p in mine_k])
        m.add(load <= kitchens[k].max_portions)
        if fair:
            d = m.new_int_var(0, upper[k], "")
            m.add(d >= before[k] + load - share)
            m.add(d >= share - before[k] - load)
            devs.append(d)
    if fair:
        worst = m.new_int_var(0, max(upper.values()), "")
        for d in devs:
            m.add(worst >= d)
        objective += weight * n * worst + weight * cp_model.LinearExpr.sum(devs)
    m.minimize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.random_seed = 7
    if deterministic:
        # The same plan on every run and machine (the eval harness needs this): one worker, and
        # the limit counts work done rather than wall-clock seconds, so the build time is not
        # subtracted. One worker rather than the planner's interleaved eight: on this model the
        # interleaved search took 25-60x longer for the same optimum.
        solver.parameters.num_workers = 1
        solver.parameters.max_deterministic_time = req.time_limit_s
    else:
        left = req.time_limit_s - (time.perf_counter() - started)
        if left < MIN_SOLVER_S:
            return None
        solver.parameters.num_workers = 8
        solver.parameters.max_time_in_seconds = left
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {
        c.order: c.kitchen for mine in by_order.values() for v, c in mine if solver.boolean_value(v)
    }


def gini(values: list[int]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    xs = sorted(values)
    n = len(xs)
    cumulative = sum((i + 1) * x for i, x in enumerate(xs))
    return round((2 * cumulative) / (n * sum(xs)) - (n + 1) / n, 3)


def summarise(req: AssignRequest, chosen: dict[int, int], pairs: list[Candidate]) -> AssignSummary:
    """Per-portion figures are over the assigned portions; `gini` is over every kitchen in the
    request, `gini_eligible` over the kitchens that were a candidate for some order."""
    by_pair = {(c.order, c.kitchen): c for c in pairs}
    portions = sum(req.orders[o].portions for o in chosen)
    km = sum(by_pair[o, k].metres / 1000 * req.orders[o].portions for o, k in chosen.items())
    cost = sum(by_pair[o, k].cost_paise * req.orders[o].portions for o, k in chosen.items())
    earnings = [k.earnings_paise for k in req.kitchens]
    for o, k in chosen.items():
        earnings[k] += req.orders[o].portions * req.paise_per_portion
    eligible = sorted({c.kitchen for c in pairs})
    return AssignSummary(
        portions=portions,
        kitchens_used=len(set(chosen.values())),
        km_per_portion=round(km / max(portions, 1), 3),
        cost_paise_per_portion=round(cost / max(portions, 1)),
        gini=gini(earnings),
        gini_eligible=gini([earnings[k] for k in eligible]),
        earnings_paise=[
            KitchenEarningsOut(kitchen_id=k.id, earnings_paise=e)
            for k, e in zip(req.kitchens, earnings, strict=True)
        ],
    )


def answer(
    req: AssignRequest,
    pairs: list[Candidate],
    chosen: dict[int, int],
    *,
    solved: bool,
    started: float,
) -> AssignResponse:
    has_pair = {c.order for c in pairs}
    unassigned = [
        UnassignedOut(order_id=order.id, reason="no_room" if o in has_pair else why_not(order, req))
        for o, order in enumerate(req.orders)
        if o not in chosen
    ]
    if not chosen:
        status = "infeasible"
    elif unassigned:
        status = "partial"
    else:
        status = "solved" if solved else "fallback"
    by_pair = {(c.order, c.kitchen): c for c in pairs}
    return AssignResponse(
        status=status,
        assignments=[
            AssignmentOut(
                order_id=req.orders[o].id,
                kitchen_id=req.kitchens[k].id,
                portions=req.orders[o].portions,
                km=round(by_pair[o, k].metres / 1000, 2),
            )
            for o, k in sorted(chosen.items())
        ],
        unassigned=unassigned,
        summary=summarise(req, chosen, pairs) if chosen else None,
        seconds=round(time.perf_counter() - started, 3),
        model=MODEL if solved else GREEDY,
    )


def assign(req: AssignRequest, *, deterministic: bool = True) -> AssignResponse:
    """The fair plan. `deterministic` (the default, for in-process callers such as the eval
    harness) gives the same plan on every run; the endpoint turns it off so that the time limit
    is wall clock. `model` names what answered: the CP-SAT model or the greedy fallback."""
    started = time.perf_counter()
    pairs = candidates(req)
    chosen = None
    first = greedy(req, pairs)
    if pairs and len(req.orders) <= req.max_orders_for_solver:
        chosen = solve(req, pairs, started=started, deterministic=deterministic, hint=first)
    if chosen is None:
        return answer(req, pairs, first, solved=False, started=started)
    return answer(req, pairs, chosen, solved=True, started=started)


def baseline(req: AssignRequest) -> AssignResponse:
    """Greedy nearest kitchen, for the eval harness's comparison."""
    started = time.perf_counter()
    pairs = candidates(req)
    return answer(req, pairs, greedy(req, pairs), solved=False, started=started)
