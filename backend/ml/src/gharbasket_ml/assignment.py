"""Fair kitchen assignment (M10 §3.5 `POST /assign/kitchens`, handbook 08 M-G).

Tomorrow's tiffin orders (portions at a society) go to home kitchens that can cook them: within
their capacity for the meal, with the menu skills the order needs, above the rating floor and
within reach. The CP-SAT model minimises travel distance plus a quality penalty, plus **λ times
the earnings inequality**: the largest deviation of any kitchen's rolling earnings (the last
weeks plus tonight's assignment) from the fair share, a linear proxy for the Gini coefficient.
λ = 0 is the cost-only plan; the λ sweep (eval harness) draws the Pareto curve for the paper.

Greedy nearest kitchen is the baseline and the answer when the solver finds nothing.
"""

import time
from dataclasses import dataclass

from ortools.sat.python import cp_model

from gharbasket_ml.routing import haversine_m
from gharbasket_ml.schemas import (
    AssignmentOut,
    AssignRequest,
    AssignResponse,
    AssignSummary,
    KitchenIn,
    TiffinOrderIn,
)

MODEL = "assignment-cpsat-v0"
PAISE_PER_PORTION_KM = 300  # the rider's cost of a kilometre, spread over a portion (₹3)


@dataclass(frozen=True)
class Candidate:
    order: int
    kitchen: int
    metres: int
    cost_paise: int  # distance and quality, per portion


def can_cook(order: TiffinOrderIn, kitchen: KitchenIn, req: AssignRequest) -> bool:
    if kitchen.rating < req.rating_floor:
        return False
    if order.skills and not set(order.skills) <= set(kitchen.skills):
        return False
    return haversine_m((order.lat, order.lng), (kitchen.lat, kitchen.lng)) <= req.max_km * 1000


def candidates(req: AssignRequest) -> list[Candidate]:
    out = []
    for o, order in enumerate(req.orders):
        for k, kitchen in enumerate(req.kitchens):
            if not can_cook(order, kitchen, req):
                continue
            metres = round(haversine_m((order.lat, order.lng), (kitchen.lat, kitchen.lng)))
            quality = round((5.0 - kitchen.rating) * req.quality_paise_per_star)
            cost = metres * PAISE_PER_PORTION_KM // 1000 + quality
            out.append(Candidate(o, k, metres, cost))
    return out


def greedy(req: AssignRequest, pairs: list[Candidate]) -> dict[int, int] | None:
    """Each order to its nearest kitchen with room, biggest orders first; None when one order
    fits nowhere."""
    room = [k.max_portions for k in req.kitchens]
    by_order: dict[int, list[Candidate]] = {}
    for c in pairs:
        by_order.setdefault(c.order, []).append(c)
    chosen: dict[int, int] = {}
    for o in sorted(range(len(req.orders)), key=lambda o: -req.orders[o].portions):
        fits = [
            c
            for c in sorted(by_order.get(o, []), key=lambda c: (c.cost_paise, c.kitchen))
            if room[c.kitchen] >= req.orders[o].portions
        ]
        if not fits:
            return None
        chosen[o] = fits[0].kitchen
        room[fits[0].kitchen] -= req.orders[o].portions
    return chosen


def solve(req: AssignRequest, pairs: list[Candidate], time_limit_s: float) -> dict[int, int] | None:
    m = cp_model.CpModel()
    x = {(c.order, c.kitchen): m.new_bool_var(f"x_{c.order}_{c.kitchen}") for c in pairs}
    orders, kitchens = req.orders, req.kitchens
    for o in range(len(orders)):
        mine = [v for (oo, _), v in x.items() if oo == o]
        if not mine:
            return None
        m.add_exactly_one(mine)
    earnings = []
    for k, kitchen in enumerate(kitchens):
        portions = sum(orders[o].portions * v for (o, kk), v in x.items() if kk == k)
        m.add(portions <= kitchen.max_portions)
        tonight = sum(
            orders[o].portions * req.paise_per_portion * v for (o, kk), v in x.items() if kk == k
        )
        earnings.append(kitchen.earnings_paise + tonight)
    cost = sum(c.cost_paise * orders[c.order].portions * x[c.order, c.kitchen] for c in pairs)
    if req.fairness_lambda > 0 and kitchens:
        total = sum(k.earnings_paise for k in kitchens) + sum(
            o.portions * req.paise_per_portion for o in orders
        )
        share = total // len(kitchens)
        deviation = m.new_int_var(0, max(total, 1), "deviation")
        for e in earnings:
            m.add(e - share <= deviation)
            m.add(share - e <= deviation)
        # Integers only: the cost is scaled by 100 so that λ keeps two decimals.
        objective = 100 * cost + round(req.fairness_lambda * 100) * deviation
    else:
        objective = 100 * cost
    m.minimize(objective)
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = 7
    solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return {o: k for (o, k), v in x.items() if solver.value(v)}


def gini(values: list[int]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    xs = sorted(values)
    n = len(xs)
    cumulative = sum((i + 1) * x for i, x in enumerate(xs))
    return round((2 * cumulative) / (n * sum(xs)) - (n + 1) / n, 3)


def summarise(req: AssignRequest, chosen: dict[int, int], pairs: list[Candidate]) -> AssignSummary:
    by_pair = {(c.order, c.kitchen): c for c in pairs}
    portions = sum(o.portions for o in req.orders)
    km = sum(by_pair[o, k].metres / 1000 * req.orders[o].portions for o, k in chosen.items())
    cost = sum(by_pair[o, k].cost_paise * req.orders[o].portions for o, k in chosen.items())
    earnings = [k.earnings_paise for k in req.kitchens]
    for o, k in chosen.items():
        earnings[k] += req.orders[o].portions * req.paise_per_portion
    return AssignSummary(
        portions=portions,
        kitchens_used=len(set(chosen.values())),
        km_per_portion=round(km / max(portions, 1), 3),
        cost_paise_per_portion=round(cost / max(portions, 1)),
        gini=gini(earnings),
        earnings_paise=earnings,
    )


def assign(req: AssignRequest) -> AssignResponse:
    started = time.perf_counter()
    pairs = candidates(req)
    chosen = None
    status = "solved"
    if pairs and len(req.orders) <= req.max_orders_for_solver:
        chosen = solve(req, pairs, req.time_limit_s)
    if chosen is None:
        chosen, status = greedy(req, pairs), "fallback"
    if chosen is None:
        return AssignResponse(
            status="infeasible",
            assignments=[],
            summary=None,
            seconds=round(time.perf_counter() - started, 3),
            model=MODEL,
        )
    by_pair = {(c.order, c.kitchen): c for c in pairs}
    return AssignResponse(
        status=status,  # pyright: ignore[reportArgumentType]
        assignments=[
            AssignmentOut(
                order_id=req.orders[o].id,
                kitchen_id=req.kitchens[k].id,
                portions=req.orders[o].portions,
                km=round(by_pair[o, k].metres / 1000, 2),
            )
            for o, k in sorted(chosen.items())
        ],
        summary=summarise(req, chosen, pairs),
        seconds=round(time.perf_counter() - started, 3),
        model=MODEL,
    )


def baseline(req: AssignRequest) -> AssignResponse:
    """Greedy nearest kitchen, for the eval harness's comparison."""
    pairs = candidates(req)
    chosen = greedy(req, pairs)
    if chosen is None:
        return AssignResponse(
            status="infeasible", assignments=[], summary=None, seconds=0.0, model="greedy-v0"
        )
    by_pair = {(c.order, c.kitchen): c for c in pairs}
    return AssignResponse(
        status="fallback",
        assignments=[
            AssignmentOut(
                order_id=req.orders[o].id,
                kitchen_id=req.kitchens[k].id,
                portions=req.orders[o].portions,
                km=round(by_pair[o, k].metres / 1000, 2),
            )
            for o, k in sorted(chosen.items())
        ],
        summary=summarise(req, chosen, pairs),
        seconds=0.0,
        model="greedy-v0",
    )
