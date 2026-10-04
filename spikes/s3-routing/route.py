"""Spike S-3: route one morning slot of society drops with OR-Tools and OSRM, against baselines.

Run from the repository root, with OSRM up (`just osrm-prepare` once, then `just up-routing`):
    uv run --with ortools==9.15.6755 python spikes/s3-routing/route.py
    uv run --with ortools==9.15.6755 python spikes/s3-routing/route.py --scale   # 100 and 200 stops

Throwaway code (excluded from lint). Findings: docs/spikes/S-3-routing.md.

A stop is a society, not a flat: vans hand crates to the Society Captain, who does the last 50 m.
Every method is scored by the same `evaluate()`, which replays the routes on the OSRM matrix.
"""

import argparse
import json
import math
import random
import time
import urllib.request
from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

OSRM = "http://localhost:5050"
SEED = 7

# Places (lat, lng). The hub is a synthetic point in Kharghar; the food bank is in CBD Belapur.
HUB = (19.0300, 73.0690)
FOOD_BANK = (19.0190, 73.0390)
# The five seed societies with boundaries (gharbasket.tools.seed.SOCIETIES), then random ones.
SEED_SOCIETIES = [
    ("Sunrise Towers", 19.0395, 73.0675),
    ("Green Valley Heights", 19.0265, 73.0590),
    ("Shree Ganesh CHS", 19.0430, 73.0720),
    ("Panchavati Residency", 19.0190, 73.0960),
    ("Krishna Kunj", 19.0005, 73.1185),
]
# Kharghar, Kamothe, Kalamboli, Khandeshwar, New Panvel, Belapur (about 9 x 10 km).
AREA = (18.995, 19.055, 73.035, 73.125)
MAX_SNAP_M = 120  # a random point further than this from a road is a hill or the creek: resample

# Time: seconds after 06:00 IST.
T = lambda h, m=0: int((h - 6) * 3600 + m * 60)  # noqa: E731
DEPART = T(6, 30)  # vans are loaded by 06:30
RETURN_BY = T(11, 30)
WINDOWS = [(T(7), T(9)), (T(8), T(10))]  # captain's handover window, picked per society
FOOD_BANK_WINDOW = (T(8), T(12))

# Vehicles and money (assumptions to confirm in the pilot; M7 open question "vehicle type").
VAN = {"kg": 400, "crates": 24, "fixed_paise": 25_000, "paise_per_km": 60, "speed": 1.5}
BIKE = {"paise_per_km": 250, "speed": 1.2, "service_s": 240}  # one trip per order
WAGE_PAISE_PER_S = 3  # ₹108 per hour, for van riders and bike riders alike
LATE_PAISE_PER_S = 50  # ₹30 per minute late: worth more than the wage, so lateness is a last resort
ORDERS_PER_CRATE = 3
RELOAD_S = 20 * 60  # back at the hub between two trips: unload empties, load the next trip


@dataclass
class Stop:
    name: str
    lat: float
    lng: float
    kind: str  # "hub", "drop" (society) or "bank" (food bank, one copy per donation)
    orders: int = 0
    kg: int = 0  # delivered here (tenths of a kg)
    crates: int = 0  # delivered here
    empties: int = 0  # empty crates collected here (they ride back to the hub)
    donation_kg: int = 0  # picked up here for the food bank (tenths of a kg), takes one crate
    donation_of: int | None = None  # for a "bank" copy: the society stop it serves
    window: tuple[int, int] = (0, RETURN_BY)

    @property
    def service_s(self) -> int:
        if self.kind == "bank":
            return 180
        if self.kind == "hub":
            return 0
        return 240 + 30 * (self.crates + self.empties)


@dataclass
class Instance:
    stops: list[Stop]  # 0 = hub
    dist: list[list[int]]  # metres
    dur: list[list[int]]  # seconds, car (no traffic)
    vans: int

    def van_s(self, i: int, j: int) -> int:
        return round(self.dur[i][j] * VAN["speed"])

    @property
    def orders(self) -> int:
        return sum(s.orders for s in self.stops)


# ---------------------------------------------------------------------------------------------
# Instance: societies snapped to roads, demand, windows, donations; matrix from OSRM


def osrm(path: str) -> dict:
    with urllib.request.urlopen(f"{OSRM}{path}", timeout=60) as r:
        return json.load(r)


def snap(lat: float, lng: float) -> tuple[float, float, float]:
    w = osrm(f"/nearest/v1/driving/{lng},{lat}?number=1")["waypoints"][0]
    return w["location"][1], w["location"][0], w["distance"]


def build(n_stops: int, seed: int = SEED, donations: int = 8) -> Instance:
    rnd = random.Random(seed)
    lat, lng, _ = snap(*HUB)
    stops = [Stop("hub", lat, lng, "hub")]
    places = list(SEED_SOCIETIES[:n_stops])
    while len(places) < n_stops:
        p = (rnd.uniform(AREA[0], AREA[1]), rnd.uniform(AREA[2], AREA[3]))
        if snap(*p)[2] <= MAX_SNAP_M:
            places.append((f"Society {len(places) + 1}", *p))
    for name, la, ln in places:
        la, ln, _ = snap(la, ln)
        orders = rnd.randint(4, 20)
        crates = math.ceil(orders / ORDERS_PER_CRATE)
        stops.append(
            Stop(
                name,
                la,
                ln,
                "drop",
                orders=orders,
                kg=sum(rnd.randint(30, 60) for _ in range(orders)),  # 3 to 6 kg an order
                crates=crates,
                # 70 % of societies were served yesterday and hand back their empties.
                empties=crates if rnd.random() < 0.7 else 0,
                window=rnd.choice(WINDOWS),
            )
        )
    lat, lng, _ = snap(*FOOD_BANK)
    for i in rnd.sample(range(1, n_stops + 1), min(donations, n_stops)):
        stops[i].donation_kg = rnd.randint(50, 200)
        stops.append(Stop("food bank", lat, lng, "bank", donation_of=i, window=FOOD_BANK_WINDOW))
    coords = ";".join(f"{s.lng},{s.lat}" for s in stops)
    t = osrm(f"/table/v1/driving/{coords}?annotations=duration,distance")
    dist = [[round(x) for x in row] for row in t["distances"]]
    dur = [[round(x) for x in row] for row in t["durations"]]
    vans = math.ceil(sum(s.crates for s in stops) / VAN["crates"] * 1.5)
    return Instance(stops, dist, dur, vans)


# ---------------------------------------------------------------------------------------------
# One scorer for every method


@dataclass
class Route:
    stops: list[int]  # without the hub at either end
    start: int = DEPART
    van: int = -1  # which physical van drives it (-1: one route per van, or by trip order)


@dataclass
class Result:
    name: str
    vehicles: int
    km: float
    hours: float
    cost_paise: int
    on_time: float  # share of society stops reached within their window
    late_min: float
    seconds: float
    problems: list[str] = field(default_factory=list)
    trace: list[tuple[float, int]] = field(default_factory=list)  # (seconds, objective) for CP


def evaluate(
    inst: Instance, name: str, routes: list[Route], seconds: float, trips: int = 1
) -> Result:
    """Replay routes on the matrix: times (waiting when early), loads, pairing, cost.

    With trips > 1, routes[k*trips : (k+1)*trips] are one van's trips, unless `Route.van` says.
    """
    S = inst.stops
    problems: list[str] = []
    seen: list[int] = []
    km = hours = cost = late = 0.0
    on_time = drops = 0
    for v, r in enumerate(routes):
        if r.van < 0:
            r.van = v // trips
    vans = len({r.van for r in routes if r.stops})
    back_at: dict[int, int] = {}
    for v, r in sorted(enumerate(routes), key=lambda x: (x[1].van, x[1].start)):
        if not r.stops:
            continue
        van = r.van
        if van in back_at and r.start < back_at[van] + RELOAD_S:
            problems.append(f"van {van}: trip leaves before the previous one is back and reloaded")
        # The van leaves with everything it delivers today; empties and donations come aboard.
        kg = sum(S[i].kg for i in r.stops)
        crates = sum(S[i].crates for i in r.stops)
        onboard: set[int] = set()
        t, here, metres = r.start, 0, 0
        for i in r.stops:
            t += inst.van_s(here, i)
            metres += inst.dist[here][i]
            s = S[i]
            if t < s.window[0]:
                t = s.window[0]
            if s.kind == "drop":
                drops += 1
                if t <= s.window[1]:
                    on_time += 1
                else:
                    late += t - s.window[1]
                kg += s.donation_kg - s.kg
                crates += s.empties - s.crates + (1 if s.donation_kg else 0)
                if s.donation_kg:
                    onboard.add(i)
            elif s.kind == "bank":
                if s.donation_of not in onboard:
                    problems.append(f"van {v}: food bank before its pickup {s.donation_of}")
                onboard.discard(s.donation_of)
                kg -= S[s.donation_of].donation_kg
                crates -= 1
            if kg > VAN["kg"] * 10 or crates > VAN["crates"]:
                problems.append(f"van {v}: over capacity at {s.name} ({kg / 10} kg, {crates} cr)")
            t += s.service_s
            here = i
            seen.append(i)
        if onboard:
            problems.append(f"van {v}: donations never reached the food bank: {sorted(onboard)}")
        t += inst.van_s(here, 0)
        metres += inst.dist[here][0]
        if t > RETURN_BY:
            problems.append(f"van {v}: back at the hub after 11:30")
        back_at[van] = t
        km += metres / 1000
        hours += (t - r.start) / 3600
        cost += metres / 1000 * VAN["paise_per_km"]
        cost += (t - r.start) * WAGE_PAISE_PER_S
    missing = set(range(1, len(S))) - set(seen)
    if missing or len(seen) != len(set(seen)):
        problems.append(f"stops missing {sorted(missing)} or visited twice")
    cost += vans * VAN["fixed_paise"]
    return Result(
        name,
        vans,
        km,
        hours,
        round(cost),
        on_time / max(drops, 1),
        late / 60,
        seconds,
        problems,
    )


# ---------------------------------------------------------------------------------------------
# Baseline 1: one trip per order (a bike rider per order, hub → society → hub)


def one_trip_per_order(inst: Instance) -> tuple[Result, int]:
    """The smallest bike fleet that gets 95 % of orders on time, dispatched earliest-deadline first.

    Riders are paid only while busy, which flatters this baseline.
    """
    started = time.perf_counter()
    orders = sorted(
        (s.window[1], s.window[0], i) for i, s in enumerate(inst.stops) for _ in range(s.orders)
    )
    for riders in range(1, len(orders) + 1):
        free = [DEPART] * riders
        on, km, busy = 0, 0.0, 0
        for end, start, i in orders:
            r = min(range(riders), key=free.__getitem__)
            go = round(inst.dur[0][i] * BIKE["speed"])
            back = round(inst.dur[i][0] * BIKE["speed"])
            arrive = max(free[r] + go, start)
            on += arrive <= end
            free[r] = arrive + BIKE["service_s"] + back
            km += (inst.dist[0][i] + inst.dist[i][0]) / 1000
            busy += go + BIKE["service_s"] + back
        if on / len(orders) >= 0.95:
            break
    cost = busy * WAGE_PAISE_PER_S + km * BIKE["paise_per_km"]
    res = Result(
        "One trip per order (bikes)",
        riders,
        km,
        busy / 3600,
        round(cost),
        on / len(orders),
        0,
        time.perf_counter() - started,
    )
    return res, len(orders)


# ---------------------------------------------------------------------------------------------
# Baseline 2: nearest neighbour with the same vans (ignores windows)


def nearest_neighbour(inst: Instance) -> Result:
    started = time.perf_counter()
    S = inst.stops
    left = {i for i, s in enumerate(S) if s.kind == "drop"}
    routes: list[Route] = []
    while left:
        route, here, kg, crates, carrying = [], 0, 0, 0, []
        while True:
            # Room is tight at the start (everything aboard): pick the stops first, then check.
            fits = [
                i
                for i in left
                if kg + S[i].kg <= VAN["kg"] * 10 and crates + S[i].crates <= VAN["crates"]
            ]
            if not fits:
                break
            i = min(fits, key=lambda j: inst.dur[here][j])
            route.append(i)
            left.discard(i)
            kg += S[i].kg
            crates += S[i].crates
            here = i
            if S[i].donation_kg:
                carrying.append(i)
        # Empties and donations take the room the drops freed, so the loads above stay valid.
        banks = [j for j, s in enumerate(S) if s.kind == "bank" and s.donation_of in carrying]
        # Leave just in time for the first society's window instead of idling from 06:30.
        start = max(DEPART, S[route[0]].window[0] - inst.van_s(0, route[0]))
        routes.append(Route(route + banks, start))
    return evaluate(inst, "Nearest neighbour (vans)", routes, time.perf_counter() - started)


# ---------------------------------------------------------------------------------------------
# OR-Tools: CVRPTW with pickups (empties) and paired donation pickup → food bank


def solve(
    inst: Instance,
    limit_s: int,
    trips: int = 1,
    fleet: int | None = None,
    max_trip_s: int | None = None,
    free_from: list[int] | None = None,
) -> Result:
    """`trips` = 2 lets each of `fleet` vans come back, reload and go out again within the slot.

    A van's trips are consecutive vehicles in the model. With one trip the fixed cost per van
    lets the solver pick the fleet; with two, the fleet is fixed (see `fewest_vans`), because
    "a second trip only if the first ran" stops every first-solution heuristic from starting.
    """
    started = time.perf_counter()
    S = inst.stops
    n = len(S)
    vans = inst.vans if trips == 1 else (fleet or inst.vans) * trips
    if max_trip_s:  # short trips, to be packed onto vans afterwards: no fixed cost per trip
        vans = inst.vans * 3
    if free_from is not None:  # vans already out this morning (free from then, already paid for)
        vans = len(free_from) + inst.vans
    manager = pywrapcp.RoutingIndexManager(n, vans, 0)
    routing = pywrapcp.RoutingModel(manager)
    node = manager.IndexToNode

    def arc_cost(a: int, b: int) -> int:
        return inst.dist[node(a)][node(b)] * VAN["paise_per_km"] // 1000

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(arc_cost))
    for v in range(vans):
        fixed = trips == 1 and not max_trip_s and not (free_from and v < len(free_from))
        routing.SetFixedCostOfVehicle(VAN["fixed_paise"] if fixed else 0, v)

    def travel(a: int, b: int) -> int:
        i, j = node(a), node(b)
        return S[i].service_s + inst.van_s(i, j)

    routing.AddDimension(routing.RegisterTransitCallback(travel), 3600, RETURN_BY, False, "time")
    tdim = routing.GetDimensionOrDie("time")
    tdim.SetSpanCostCoefficientForAllVehicles(WAGE_PAISE_PER_S)
    for i, s in enumerate(S):
        if i == 0:
            continue
        idx = manager.NodeToIndex(i)
        # Never before the captain is there; late is allowed but costs ₹30 a minute.
        tdim.CumulVar(idx).SetRange(s.window[0], RETURN_BY)
        tdim.SetCumulVarSoftUpperBound(idx, s.window[1], LATE_PAISE_PER_S)
    for v in range(vans):
        earliest = free_from[v] if free_from and v < len(free_from) else DEPART
        tdim.CumulVar(routing.Start(v)).SetRange(min(earliest, RETURN_BY), RETURN_BY)
        routing.AddVariableMaximizedByFinalizer(tdim.CumulVar(routing.Start(v)))
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(routing.End(v)))
        if max_trip_s:
            tdim.SetSpanUpperBoundForVehicle(max_trip_s, v)
        if v % trips:  # a later trip of the same van leaves after the previous one is back
            routing.solver().Add(
                tdim.CumulVar(routing.Start(v)) >= tdim.CumulVar(routing.End(v - 1)) + RELOAD_S
            )

    # Load = what is still to be delivered + what was collected. The van leaves full of its own
    # drops, so the start load is a variable tied to the route's total ("drops" dimension).
    def load_dims(name: str, cap: int, drop, picked) -> None:
        dcb = routing.RegisterUnaryTransitCallback(lambda a: drop(S[node(a)]))
        lcb = routing.RegisterUnaryTransitCallback(lambda a: picked(S[node(a)]) - drop(S[node(a)]))
        routing.AddDimension(dcb, 0, cap, True, f"{name}_drops")
        # Slack 0, start not fixed: the start load is free in [0, cap] and pinned below.
        routing.AddDimension(lcb, 0, cap, False, f"{name}_load")
        drops = routing.GetDimensionOrDie(f"{name}_drops")
        load = routing.GetDimensionOrDie(f"{name}_load")
        for v in range(vans):
            routing.solver().Add(load.CumulVar(routing.Start(v)) == drops.CumulVar(routing.End(v)))

    def kg_picked(s: Stop) -> int:
        return s.donation_kg if s.kind == "drop" else -S[s.donation_of].donation_kg

    def kg_drop(s: Stop) -> int:
        return s.kg

    def cr_picked(s: Stop) -> int:
        if s.kind == "bank":
            return -1
        return s.empties + (1 if s.donation_kg else 0)

    def cr_drop(s: Stop) -> int:
        return s.crates

    load_dims("kg", VAN["kg"] * 10, kg_drop, lambda s: kg_picked(s) if s.kind != "hub" else 0)
    load_dims("crates", VAN["crates"], cr_drop, lambda s: cr_picked(s) if s.kind != "hub" else 0)

    for j, s in enumerate(S):
        if s.kind == "bank":
            p, d = manager.NodeToIndex(s.donation_of), manager.NodeToIndex(j)
            routing.AddPickupAndDelivery(p, d)
            routing.solver().Add(routing.VehicleVar(p) == routing.VehicleVar(d))
            routing.solver().Add(tdim.CumulVar(p) <= tdim.CumulVar(d))

    trace: list[tuple[float, int]] = []
    routing.AddAtSolutionCallback(
        lambda: trace.append((time.perf_counter() - started, routing.CostVar().Value()))
    )
    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromSeconds(limit_s)
    solution = routing.SolveWithParameters(params)
    seconds = time.perf_counter() - started
    if solution is None:
        return Result(f"OR-Tools {limit_s} s", 0, 0, 0, 0, 0, 0, seconds, ["no solution"])
    routes = []
    for v in range(vans):
        idx, seq = solution.Value(routing.NextVar(routing.Start(v))), []
        while not routing.IsEnd(idx):
            seq.append(node(idx))
            idx = solution.Value(routing.NextVar(idx))
        routes.append(Route(seq, solution.Value(tdim.CumulVar(routing.Start(v)))))
    label = f"OR-Tools CVRPTW ({limit_s} s)"
    if trips > 1:
        label = f"OR-Tools, {fleet} vans x 2 trips ({limit_s} s)"
    if max_trip_s:
        label = f"OR-Tools trips of <= {max_trip_s // 60} min ({limit_s} s)"
    res = evaluate(inst, label, routes, seconds, trips)
    res.trace = trace
    res.routes = routes  # type: ignore[attr-defined]
    return res


def fewest_vans(inst: Instance, limit_s: int) -> tuple[Result | None, list[int]]:
    """The smallest fleet that, with two trips each, serves every society on time (5 s probes)."""
    crates = sum(s.crates for s in inst.stops)
    tried = []
    for fleet in range(math.ceil(crates / VAN["crates"] / 2), inst.vans // 2 + 2):
        tried.append(fleet)
        probe = solve(inst, 5, trips=2, fleet=fleet)
        if probe.on_time == 1 and not probe.problems:
            return solve(inst, limit_s, trips=2, fleet=fleet), tried
    return None, tried


def route_end(inst: Instance, r: Route) -> int:
    t, here = r.start, 0
    for i in r.stops:
        t = max(t + inst.van_s(here, i), inst.stops[i].window[0]) + inst.stops[i].service_s
        here = i
    return t + inst.van_s(here, 0)


def pack(inst: Instance, res: Result) -> Result:
    """Two-phase multi-trip: keep the one-trip routes and times, then put routes that do not
    overlap (with the reload gap) on the same van. Greedy interval partitioning, best fit."""
    started = time.perf_counter()
    routes = sorted(
        (Route(r.stops, r.start) for r in res.routes if r.stops),  # type: ignore[attr-defined]
        key=lambda r: r.start,
    )
    free_at: list[int] = []
    for r in routes:
        fits = [k for k, t in enumerate(free_at) if t + RELOAD_S <= r.start]
        r.van = max(fits, key=free_at.__getitem__) if fits else len(free_at)
        if not fits:
            free_at.append(0)
        free_at[r.van] = route_end(inst, r)
    name = f"{res.name}, then routes packed onto vans"
    packed = evaluate(inst, name, routes, res.seconds + time.perf_counter() - started)
    packed.routes = routes  # type: ignore[attr-defined]
    return packed


def subset(inst: Instance, keep: list[int]) -> Instance:
    """The hub plus `keep` (and the food-bank copies of their donations), renumbered."""
    banks = [j for j, s in enumerate(inst.stops) if s.kind == "bank" and s.donation_of in keep]
    nodes = [0, *keep, *banks]
    new = {old: k for k, old in enumerate(nodes)}
    stops = []
    for old in nodes:
        s = inst.stops[old]
        donation_of = new[s.donation_of] if s.donation_of is not None else None
        stops.append(Stop(**{**s.__dict__, "donation_of": donation_of}))
    dist = [[inst.dist[a][b] for b in nodes] for a in nodes]
    dur = [[inst.dur[a][b] for b in nodes] for a in nodes]
    sub = Instance(stops, dist, dur, inst.vans)
    sub.nodes = nodes  # type: ignore[attr-defined]
    return sub


def waves(inst: Instance, limit_s: int) -> Result:
    """Multi-trip by decomposition: route the 07-09 societies, then the 08-10 ones with the same
    vans once they are back and reloaded (new vans only if needed). Each wave is a plain CVRPTW."""
    started = time.perf_counter()
    early = [i for i, s in enumerate(inst.stops) if s.kind == "drop" and s.window == WINDOWS[0]]
    late = [i for i, s in enumerate(inst.stops) if s.kind == "drop" and s.window != WINDOWS[0]]
    routes: list[Route] = []
    first = subset(inst, early)
    one = solve(first, limit_s // 2)
    free_from = []
    for r in (x for x in one.routes if x.stops):  # type: ignore[attr-defined]
        trip = Route([first.nodes[i] for i in r.stops], r.start, van=len(free_from))  # type: ignore[attr-defined]
        routes.append(trip)
        free_from.append(route_end(inst, trip) + RELOAD_S)
    second = subset(inst, late)
    two = solve(second, limit_s // 2, free_from=free_from)
    extra = len(free_from)
    for v, r in enumerate(two.routes):  # type: ignore[attr-defined]
        if r.stops:
            van = v if v < len(free_from) else (extra := extra + 1) - 1
            routes.append(Route([second.nodes[i] for i in r.stops], r.start, van=van))  # type: ignore[attr-defined]
    res = evaluate(
        inst, f"OR-Tools in two waves ({limit_s} s)", routes, time.perf_counter() - started
    )
    res.routes = routes  # type: ignore[attr-defined]
    return res


# ---------------------------------------------------------------------------------------------
# Report


def shift_cost(r: Result) -> float:
    shift_s = RETURN_BY - DEPART
    return (
        r.vehicles * (VAN["fixed_paise"] + shift_s * WAGE_PAISE_PER_S) + r.km * VAN["paise_per_km"]
    )


def progress(trace: list[tuple[float, int]], limit: int) -> str:
    out = [f"  first solution after {trace[0][0]:.2f} s: ₹{trace[0][1] / 100:,.0f}"]
    for mark in (1, 2, 5, 10, 20, limit):
        if mark <= limit:
            best = min(c for t, c in trace if t <= mark)
            out.append(f"  by {mark:>2} s: ₹{best / 100:,.0f}")
    return "\n".join(out)


def clock(t: int) -> str:
    return f"{6 + t // 3600:02d}:{t % 3600 // 60:02d}"


def row(r: Result, orders: int) -> str:
    return (
        f"| {r.name} | {r.vehicles} | {r.cost_paise / 100 / orders:.2f} | {r.km / orders:.2f} | "
        f"{r.km:.0f} | {r.hours:.1f} | {r.on_time * 100:.0f} % | {r.seconds:.2f} s | "
        f"{'ok' if not r.problems else '; '.join(r.problems[:2])} |"
    )


HEADER = (
    "| Method | Vehicles | ₹/order | km/order | km | Hours | On time | Time | Checks |\n"
    "|---|---|---|---|---|---|---|---|---|"
)


def describe(inst: Instance) -> str:
    drops = [s for s in inst.stops if s.kind == "drop"]
    return (
        f"{len(drops)} societies, {inst.orders} orders, "
        f"{sum(s.kg for s in drops) / 10:.0f} kg, {sum(s.crates for s in drops)} crates out, "
        f"{sum(s.empties for s in drops)} empties back, "
        f"{sum(1 for s in drops if s.donation_kg)} donation pickups for the food bank; "
        f"{inst.vans} vans available ({VAN['kg']} kg, {VAN['crates']} crates each)"
    )


def geojson(inst: Instance, routes: list[Route], path: str) -> None:
    features = []
    for v, r in enumerate(x for x in routes if x.stops):
        v = r.van if r.van >= 0 else v
        pts = [0, *r.stops, 0]
        coords = ";".join(f"{inst.stops[i].lng},{inst.stops[i].lat}" for i in pts)
        g = osrm(f"/route/v1/driving/{coords}?overview=simplified&geometries=geojson")
        features.append(
            {
                "type": "Feature",
                "properties": {"van": v + 1, "leaves": clock(r.start)},
                "geometry": g["routes"][0]["geometry"],
            }
        )
    for i, s in enumerate(inst.stops):
        if s.kind == "bank" and i != next(j for j, x in enumerate(inst.stops) if x.kind == "bank"):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {"name": s.name, "kind": s.kind, "orders": s.orders},
                "geometry": {"type": "Point", "coordinates": [round(s.lng, 5), round(s.lat, 5)]},
            }
        )
    with open(path, "w") as f:
        json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stops", type=int, default=50)
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--scale", action="store_true", help="also time 100 and 200 stops")
    ap.add_argument("--geojson", default="spikes/s3-routing/routes.geojson")
    args = ap.parse_args()

    t0 = time.perf_counter()
    inst = build(args.stops)
    print(f"Instance (seed {SEED}): {describe(inst)}")
    print(
        f"OSRM: snapping and a {len(inst.stops)}x{len(inst.stops)} table took "
        f"{time.perf_counter() - t0:.2f} s\n"
    )

    bikes, orders = one_trip_per_order(inst)
    nn = nearest_neighbour(inst)
    quick = solve(inst, 5)
    full = solve(inst, args.limit)
    packed = pack(inst, full)
    short = pack(inst, solve(inst, args.limit, max_trip_s=90 * 60))
    two, tried = fewest_vans(inst, args.limit)
    waved = waves(inst, args.limit)
    print(HEADER)
    for r in (bikes, nn, quick, full, packed, short, two, waved):
        if r:
            print(row(r, orders))
    print(f"\nBikes needed for 95 % on time: {bikes.vehicles}")
    print(f"Two trips a van: fleets tried {tried}; the first with every society on time is used")
    print("\nIf van riders are paid for the whole 06:30-11:00 shift instead of route time:")
    for r in (nn, full, two, waved):
        if r:
            print(f"  {r.name}: ₹{shift_cost(r) / 100 / orders:.2f} an order")
    print("\nOR-Tools best objective so far (₹, includes lateness penalties):")
    print(progress(full.trace, args.limit))
    for res in (full, waved):
        print(f"\nRoutes ({res.name}; FB = food bank):")
        used = sorted((r for r in res.routes if r.stops), key=lambda r: (r.van, r.start))  # type: ignore[attr-defined]
        for r in used:
            names = ["FB" if inst.stops[i].kind == "bank" else str(i) for i in r.stops]
            print(f"  van {r.van + 1}: leaves {clock(r.start)}  hub → {' → '.join(names)} → hub")
    if args.geojson:
        geojson(inst, waved.routes, args.geojson)  # type: ignore[attr-defined]
        print(f"\nMap: {args.geojson} (open in geojson.io)")

    if args.scale:
        print("\nScale (same model, vans = 1.5 x the crates needed):")
        print(HEADER)
        for n in (100, 200):
            big = build(n, seed=SEED + n, donations=n // 6)
            b, o = one_trip_per_order(big)
            print(f"<!-- {describe(big)} -->")
            cp = solve(big, args.limit)
            short = pack(big, solve(big, args.limit, max_trip_s=90 * 60))
            chained, tried = fewest_vans(big, args.limit)
            runs = (b, nearest_neighbour(big), solve(big, 5), cp, pack(big, cp), short, chained)
            for r in (*runs, waves(big, args.limit)):
                if r:
                    r.name = f"{n} stops: {r.name}"
                    print(row(r, o))
            if not chained:
                print(f"{n} stops: chained 2 trips found no all-on-time plan for fleets {tried}")
            print(f"first CP solution after {solve(big, 1).trace[0][0]:.2f} s")


if __name__ == "__main__":
    main()
