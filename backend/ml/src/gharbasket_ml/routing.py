"""Van routing for one delivery slot (M10 §3.4 `POST /route/solve`, M-F; spike S-3).

A stop is a society (the captain does the last 50 metres). Each van leaves the hub loaded with
its drops, hands crates over inside the captain's window, collects empties and donations on the
way, drops donations at the food bank and is back by the slot's end. The model is OR-Tools'
CVRPTW with pickups and deliveries, solved in **two waves** (the early-window societies first,
then the late-window ones with the same vans once they are back and reloaded, never more vans
than the fleet): each wave is a plain CVRPTW, so it keeps the solver's speed and the fleet falls
by 40-45 % (S-3).

Distances come from OSRM when the service has a road network, else from the haversine formula
scaled by a detour factor (`source: "haversine"`): worse, never blocking. OSRM gets a short
timeout so the gateway's budget holds. An independent checker replays every plan (windows,
loads, pairing, return time, fleet size) before it is answered.
"""

import asyncio
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Self

import httpx2
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from gharbasket_ml.schemas import (
    RouteRequest,
    RouteResponse,
    RouteStopOut,
    RouteSummary,
    RouteVanOut,
)

log = logging.getLogger(__name__)

MODEL = "cvrptw-waves-v0"
DETOUR = 1.3  # road distance over the straight line in a dense suburb
HAVERSINE_SPEED_MPS = 6.0  # about 22 km/h door to door, before the traffic factor
MAX_TABLE = 500  # osrm-routed --max-table-size in the image and compose
OSRM_TIMEOUT_S = 2.5  # the default of Settings.osrm_timeout_s


@dataclass
class Node:
    """A routing node: the hub (0), a society drop, or a food-bank copy for one donation."""

    id: str
    lat: float
    lng: float
    kind: str  # hub | drop | bank
    kg: int = 0  # tenths of a kg delivered
    crates: int = 0
    empties: int = 0
    donation_kg: int = 0  # tenths of a kg picked up here (one crate)
    donation_of: int | None = None  # bank copy: the drop it serves
    window: tuple[int, int] = (0, 0)

    def service_s(self, drop_s: int, per_crate_s: int) -> int:
        if self.kind == "hub":
            return 0
        if self.kind == "bank":
            return 180
        return drop_s + per_crate_s * (self.crates + self.empties)


@dataclass
class Instance:
    nodes: list[Node]
    dist: list[list[int]]  # metres
    dur: list[list[int]]  # seconds, before the traffic factor
    req: RouteRequest

    def travel_s(self, i: int, j: int) -> int:
        return round(self.dur[i][j] * self.req.vans.traffic_factor)

    def service_s(self, i: int) -> int:
        return self.nodes[i].service_s(self.req.service.drop_s, self.req.service.per_crate_s)


@dataclass
class Trip:
    van: int
    start: int
    stops: list[int]  # node indexes, hub excluded


@dataclass
class Checked:
    vans: int
    km: float
    cost_paise: int
    on_time: float
    late_min: float
    problems: list[str] = field(default_factory=list)
    etas: dict[tuple[int, int], int] = field(default_factory=dict)  # (trip index, node) → eta
    ends: list[int] = field(default_factory=list)


# --- the matrix -------------------------------------------------------------------------------


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def haversine_pair(a: tuple[float, float], b: tuple[float, float]) -> tuple[int, int]:
    """Metres and seconds between two points: the straight line times the detour factor."""
    metres = round(haversine_m(a, b) * DETOUR)
    return metres, round(metres / HAVERSINE_SPEED_MPS)


def haversine_matrix(points: list[tuple[float, float]]) -> tuple[list[list[int]], list[list[int]]]:
    dist = [[haversine_pair(a, b)[0] for b in points] for a in points]
    dur = [[round(d / HAVERSINE_SPEED_MPS) for d in row] for row in dist]
    return dist, dur


def fill_gaps(
    points: list[tuple[float, float]],
    distances: list[list[float | None]],
    durations: list[list[float | None]],
) -> tuple[list[list[int]], list[list[int]]]:
    """OSRM's table with its gaps filled: a pair it cannot route (null, e.g. a point off the
    road network) takes the haversine estimate instead of a free 0 m, 0 s hop."""
    n = len(points)
    if len(distances) != n or len(durations) != n:
        raise ValueError("the OSRM table does not match the points")
    dist: list[list[int]] = []
    dur: list[list[int]] = []
    gaps = 0
    for i in range(n):
        if len(distances[i]) != n or len(durations[i]) != n:
            raise ValueError("the OSRM table does not match the points")
        drow, trow = [], []
        for j in range(n):
            d, t = distances[i][j], durations[i][j]
            if d is None or t is None:
                gaps += 1
                d, t = haversine_pair(points[i], points[j])
            drow.append(round(d))
            trow.append(round(t))
        dist.append(drow)
        dur.append(trow)
    if gaps:
        log.warning("osrm_gaps: %d of %d pairs had no route, haversine fills them", gaps, n * n)
    return dist, dur


async def osrm_matrix(
    osrm_url: str, points: list[tuple[float, float]], timeout_s: float = OSRM_TIMEOUT_S
) -> tuple[list[list[int]], list[list[int]]] | None:
    """Road distances and times from OSRM's table service; None when it does not answer within
    `timeout_s` in all (connecting, waiting and reading together)."""
    if len(points) > MAX_TABLE:
        return None
    coords = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
    url = f"{osrm_url.rstrip('/')}/table/v1/driving/{coords}?annotations=distance,duration"
    try:
        async with asyncio.timeout(timeout_s), httpx2.AsyncClient(timeout=timeout_s) as http:
            answer = await http.get(url)
        body = answer.json()
        if answer.status_code != 200 or body.get("code") != "Ok":
            return None
        return fill_gaps(points, body["distances"], body["durations"])
    except (TimeoutError, httpx2.HTTPError, ValueError, KeyError, TypeError) as exc:
        log.warning("osrm_unavailable: %s, haversine instead", type(exc).__name__)
        return None


# --- the instance -----------------------------------------------------------------------------


def nodes_of(req: RouteRequest) -> list[Node]:
    nodes = [Node("hub", req.hub.lat, req.hub.lng, "hub")]
    for s in req.stops:
        nodes.append(
            Node(
                s.id,
                s.lat,
                s.lng,
                "drop",
                s.kg_tenths,
                s.crates,
                s.empties,
                s.donation_kg_tenths,
                None,
                (s.window_start_s, s.window_end_s),
            )
        )
    if req.food_bank is not None:
        for i, s in enumerate(req.stops, start=1):
            if s.donation_kg_tenths:
                nodes.append(
                    Node(
                        f"bank:{s.id}",
                        req.food_bank.lat,
                        req.food_bank.lng,
                        "bank",
                        donation_of=i,
                        window=(req.food_bank.window_start_s, req.food_bank.window_end_s),
                    )
                )
    return nodes


def subset(inst: Instance, keep: list[int]) -> tuple[Instance, list[int]]:
    """The hub plus `keep` (and the bank copies of their donations), renumbered."""
    banks = [j for j, n in enumerate(inst.nodes) if n.kind == "bank" and n.donation_of in keep]
    order = [0, *keep, *banks]
    new = {old: k for k, old in enumerate(order)}
    nodes = []
    for old in order:
        n = inst.nodes[old]
        donation_of = new[n.donation_of] if n.donation_of is not None else None
        nodes.append(Node(**{**n.__dict__, "donation_of": donation_of}))
    dist = [[inst.dist[a][b] for b in order] for a in order]
    dur = [[inst.dur[a][b] for b in order] for a in order]
    return Instance(nodes, dist, dur, inst.req), order


# --- the model --------------------------------------------------------------------------------


def solve_wave(
    inst: Instance, limit_s: float, free_from: list[int] | None = None
) -> list[Trip] | None:
    """One CVRPTW: vans already out this morning are free from `free_from` (and already paid
    for); new vans cost their fixed price. None when the solver finds no plan."""
    req = inst.req
    n = len(inst.nodes)
    if n == 1:
        return []
    reused = len(free_from or [])
    # The vans back from the first wave plus the rest of the fleet: never more than `count`.
    vans = reused + max(0, req.vans.count - reused)
    manager = pywrapcp.RoutingIndexManager(n, vans, 0)
    routing = pywrapcp.RoutingModel(manager)
    node = manager.IndexToNode

    def arc_cost(a: int, b: int) -> int:
        return inst.dist[node(a)][node(b)] * req.cost.paise_per_km // 1000

    routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitCallback(arc_cost))
    for v in range(vans):
        routing.SetFixedCostOfVehicle(0 if v < reused else req.cost.fixed_paise_per_van, v)

    def travel(a: int, b: int) -> int:
        i, j = node(a), node(b)
        return inst.service_s(i) + inst.travel_s(i, j)

    routing.AddDimension(
        routing.RegisterTransitCallback(travel), 3600, req.return_by_s, False, "time"
    )
    tdim = routing.GetDimensionOrDie("time")
    tdim.SetSpanCostCoefficientForAllVehicles(req.cost.wage_paise_per_s)
    for i, nd in enumerate(inst.nodes):
        if i == 0:
            continue
        idx = manager.NodeToIndex(i)
        tdim.CumulVar(idx).SetRange(nd.window[0], req.return_by_s)
        tdim.SetCumulVarSoftUpperBound(idx, nd.window[1], req.cost.late_paise_per_s)
    for v in range(vans):
        earliest = free_from[v] if free_from and v < reused else req.depart_s
        tdim.CumulVar(routing.Start(v)).SetRange(min(earliest, req.return_by_s), req.return_by_s)
        routing.AddVariableMaximizedByFinalizer(tdim.CumulVar(routing.Start(v)))
        routing.AddVariableMinimizedByFinalizer(tdim.CumulVar(routing.End(v)))

    def load_dims(name: str, cap: int, drop, picked) -> None:
        dcb = routing.RegisterUnaryTransitCallback(lambda a: drop(inst.nodes[node(a)]))
        lcb = routing.RegisterUnaryTransitCallback(
            lambda a: picked(inst.nodes[node(a)]) - drop(inst.nodes[node(a)])
        )
        routing.AddDimension(dcb, 0, cap, True, f"{name}_drops")
        routing.AddDimension(lcb, 0, cap, False, f"{name}_load")
        drops = routing.GetDimensionOrDie(f"{name}_drops")
        load = routing.GetDimensionOrDie(f"{name}_load")
        for v in range(vans):
            routing.solver().Add(load.CumulVar(routing.Start(v)) == drops.CumulVar(routing.End(v)))

    def kg_picked(nd: Node) -> int:
        if nd.kind == "drop":
            return nd.donation_kg
        if nd.kind == "bank" and nd.donation_of is not None:
            return -inst.nodes[nd.donation_of].donation_kg
        return 0

    def cr_picked(nd: Node) -> int:
        if nd.kind == "bank":
            return -1
        if nd.kind == "drop":
            return nd.empties + (1 if nd.donation_kg else 0)
        return 0

    load_dims("kg", req.vans.capacity_kg * 10, lambda nd: nd.kg, kg_picked)
    load_dims("crates", req.vans.capacity_crates, lambda nd: nd.crates, cr_picked)
    for j, nd in enumerate(inst.nodes):
        if nd.kind == "bank" and nd.donation_of is not None:
            p, d = manager.NodeToIndex(nd.donation_of), manager.NodeToIndex(j)
            routing.AddPickupAndDelivery(p, d)
            routing.solver().Add(routing.VehicleVar(p) == routing.VehicleVar(d))
            routing.solver().Add(tdim.CumulVar(p) <= tdim.CumulVar(d))

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    params.time_limit.FromMilliseconds(max(100, int(limit_s * 1000)))
    solution = routing.SolveWithParameters(params)
    if solution is None:
        return None
    trips = []
    for v in range(vans):
        idx, seq = solution.Value(routing.NextVar(routing.Start(v))), []
        while not routing.IsEnd(idx):
            seq.append(node(idx))
            idx = solution.Value(routing.NextVar(idx))
        trips.append(Trip(v, solution.Value(tdim.CumulVar(routing.Start(v))), seq))
    return trips


def trip_end(inst: Instance, trip: Trip) -> int:
    t, here = trip.start, 0
    for i in trip.stops:
        t = max(t + inst.travel_s(here, i), inst.nodes[i].window[0]) + inst.service_s(i)
        here = i
    return t + inst.travel_s(here, 0)


def waves(inst: Instance, limit_s: float) -> list[Trip] | None:
    """The early-window societies first, then the others with the same vans from the moment
    they are back and reloaded; new vans only where needed, and only those of the fleet the
    first wave left at the hub. Node indexes are the instance's."""
    drops = [i for i, n in enumerate(inst.nodes) if n.kind == "drop"]
    if not drops:
        return []
    earliest = min(inst.nodes[i].window[0] for i in drops)
    early = [i for i in drops if inst.nodes[i].window[0] == earliest]
    late = [i for i in drops if i not in early]
    if not late:
        return solve_wave(inst, limit_s)
    first, order1 = subset(inst, early)
    one = solve_wave(first, limit_s / 2)
    if one is None:
        return None
    trips: list[Trip] = []
    free_from: list[int] = []
    for t in (x for x in one if x.stops):
        trip = Trip(len(free_from), t.start, [order1[i] for i in t.stops])
        trips.append(trip)
        free_from.append(trip_end(inst, trip) + inst.req.vans.reload_s)
    second, order2 = subset(inst, late)
    two = solve_wave(second, limit_s / 2, free_from=free_from)
    if two is None:
        return None
    extra = len(free_from)
    for v, t in enumerate(two):
        if t.stops:
            van = v if v < len(free_from) else (extra := extra + 1) - 1
            trips.append(Trip(van, t.start, [order2[i] for i in t.stops]))
    return trips


# --- the checker ------------------------------------------------------------------------------


def check(inst: Instance, trips: list[Trip]) -> Checked:
    """Replay the trips on the matrix: no van leaves before the vans are loaded, windows
    (waiting when early; a drop more than `max_late_s` late is a problem), the food bank's
    closing time, loads leaving the hub and after every stop, each donation on the same van
    before its food-bank drop, every stop once, back in time, a van's next trip only after it
    is back and reloaded, and no more vans than the fleet."""
    req = inst.req
    nodes = inst.nodes
    cap_kg, cap_crates = req.vans.capacity_kg * 10, req.vans.capacity_crates
    out = Checked(0, 0.0, 0, 0.0, 0.0)
    seen: list[int] = []
    on_time = drops = 0
    late = 0.0
    cost = 0.0
    back_at: dict[int, int] = {}
    ordered = sorted(enumerate(trips), key=lambda x: (x[1].van, x[1].start))
    out.ends = [0] * len(trips)
    for k, trip in ordered:
        if not trip.stops:
            continue
        if trip.start < req.depart_s:
            out.problems.append(f"van {trip.van}: leaves before the vans are loaded")
        if trip.van in back_at and trip.start < back_at[trip.van] + req.vans.reload_s:
            out.problems.append(f"van {trip.van}: a trip leaves before the previous one is back")
        kg = sum(nodes[i].kg for i in trip.stops)
        crates = sum(nodes[i].crates for i in trip.stops)
        if kg > cap_kg or crates > cap_crates:
            out.problems.append(f"van {trip.van}: over capacity leaving the hub")
        onboard: set[int] = set()
        t, here, metres = trip.start, 0, 0
        for i in trip.stops:
            t += inst.travel_s(here, i)
            metres += inst.dist[here][i]
            nd = nodes[i]
            t = max(t, nd.window[0])
            out.etas[k, i] = t
            if nd.kind == "drop":
                drops += 1
                if t <= nd.window[1]:
                    on_time += 1
                else:
                    late += t - nd.window[1]
                    if t - nd.window[1] > req.max_late_s:
                        out.problems.append(f"{nd.id}: {(t - nd.window[1]) // 60} min late")
                kg += nd.donation_kg - nd.kg
                crates += nd.empties - nd.crates + (1 if nd.donation_kg else 0)
                if nd.donation_kg:
                    onboard.add(i)
            elif nd.kind == "bank" and nd.donation_of is not None:
                if t > nd.window[1]:
                    out.problems.append(f"van {trip.van}: reaches the food bank after it closes")
                if nd.donation_of not in onboard:
                    out.problems.append(f"van {trip.van}: food bank before its pickup")
                onboard.discard(nd.donation_of)
                kg -= nodes[nd.donation_of].donation_kg
                crates -= 1
            if kg > cap_kg or crates > cap_crates:
                out.problems.append(f"van {trip.van}: over capacity at {nd.id}")
            t += inst.service_s(i)
            here = i
            seen.append(i)
        if onboard:
            out.problems.append(f"van {trip.van}: a donation never reached the food bank")
        t += inst.travel_s(here, 0)
        metres += inst.dist[here][0]
        if t > req.return_by_s:
            out.problems.append(f"van {trip.van}: back after the slot ends")
        back_at[trip.van] = t
        out.ends[k] = t
        out.km += metres / 1000
        cost += metres / 1000 * req.cost.paise_per_km + (t - trip.start) * req.cost.wage_paise_per_s
    missing = sorted(set(range(1, len(nodes))) - set(seen))
    if missing:
        out.problems.append("not served: " + ", ".join(nodes[i].id for i in missing))
    twice = sorted({i for i in seen if seen.count(i) > 1})
    if twice:
        out.problems.append("visited twice: " + ", ".join(nodes[i].id for i in twice))
    out.vans = len({t.van for t in trips if t.stops})
    if out.vans > req.vans.count:
        out.problems.append(f"{out.vans} vans used, more vans than the fleet of {req.vans.count}")
    cost += out.vans * req.cost.fixed_paise_per_van
    out.cost_paise = round(cost)
    out.on_time = on_time / max(drops, 1)
    out.late_min = late / 60
    return out


# --- the service ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Load:
    """A trip's load so far: the peak (leaving the hub or after any stop) and the load after
    the last stop. Another stop adds its crates to everything before it (they leave the hub
    with the van) and its pickups (empties, a donation crate) after it."""

    kg_peak: int = 0
    kg_end: int = 0
    crates_peak: int = 0
    crates_end: int = 0

    def then(self, nd: Node) -> Self:
        picked = nd.empties + (1 if nd.donation_kg else 0)
        return type(self)(
            max(self.kg_peak + nd.kg, self.kg_end + nd.donation_kg),
            self.kg_end + nd.donation_kg,
            max(self.crates_peak + nd.crates, self.crates_end + picked),
            self.crates_end + picked,
        )

    def within(self, cap_kg: int, cap_crates: int) -> bool:
        return self.kg_peak <= cap_kg and self.crates_peak <= cap_crates


def nearest_neighbour(inst: Instance) -> list[Trip]:
    """The baseline (and the answer when the solver finds nothing). A trip takes the nearest
    unserved society that still fits, with the load on board tracked as the checker does
    (crates out, empties and donation crates in), then its donations to the food bank. Each
    trip goes to the van of the fleet that is free first, leaving just in time for its first
    window; a van goes out again only if it is back by the slot's end. Whatever is left is not
    placed, and the checker names it."""
    req = inst.req
    nodes = inst.nodes
    cap_kg, cap_crates = req.vans.capacity_kg * 10, req.vans.capacity_crates
    todo = [i for i, n in enumerate(nodes) if n.kind == "drop"]
    banks = {n.donation_of: j for j, n in enumerate(nodes) if n.kind == "bank"}
    free_at = [req.depart_s] * req.vans.count
    used = [False] * req.vans.count
    trips: list[Trip] = []
    while todo:
        load, here, stops = Load(), 0, []
        while True:
            fits = [i for i in todo if load.then(nodes[i]).within(cap_kg, cap_crates)]
            if not fits:
                break
            i = min(fits, key=lambda i: inst.dist[here][i])
            load = load.then(nodes[i])
            todo.remove(i)
            stops.append(i)
            here = i
        if not stops:
            break  # every society left is bigger than a van
        stops += [banks[i] for i in stops if i in banks]
        van = min(range(req.vans.count), key=lambda v: free_at[v])
        first = min(nodes[i].window[0] for i in stops if nodes[i].kind == "drop")
        start = max(free_at[van], first - inst.travel_s(0, stops[0]))
        trip = Trip(van, start, stops)
        end = trip_end(inst, trip)
        if used[van] and end > req.return_by_s:
            break  # no van can take another trip in time
        used[van] = True
        free_at[van] = end + req.vans.reload_s
        trips.append(trip)
    return trips


def plan(inst: Instance, limit_s: float, source: str) -> RouteResponse:
    started = time.perf_counter()
    trips = waves(inst, limit_s)
    status = "solved"
    if trips is None:
        trips, status = nearest_neighbour(inst), "fallback"
    checked = check(inst, trips)
    vans = []
    for k, trip in enumerate(trips):
        if not trip.stops:
            continue
        vans.append(
            RouteVanOut(
                van=trip.van,
                start_s=trip.start,
                end_s=checked.ends[k],
                stops=[
                    RouteStopOut(
                        id=inst.nodes[i].id,
                        kind=inst.nodes[i].kind,  # pyright: ignore[reportArgumentType]
                        eta_s=checked.etas[k, i],
                        late_s=max(0, checked.etas[k, i] - inst.nodes[i].window[1]),
                    )
                    for i in trip.stops
                ],
            )
        )
    return RouteResponse(
        status=status,  # pyright: ignore[reportArgumentType]
        source=source,  # pyright: ignore[reportArgumentType]
        routes=vans,
        summary=RouteSummary(
            vans=checked.vans,
            km=round(checked.km, 2),
            cost_paise=checked.cost_paise,
            on_time=round(checked.on_time, 3),
            late_min=round(checked.late_min, 1),
            problems=checked.problems,
        ),
        seconds=round(time.perf_counter() - started, 3),
        model=MODEL,
    )


async def solve(
    req: RouteRequest, osrm_url: str | None, osrm_timeout_s: float = OSRM_TIMEOUT_S
) -> RouteResponse:
    """The endpoint: the matrix (OSRM within `osrm_timeout_s`, else haversine), then the waves
    and the checker on a worker thread so the event loop stays free."""
    nodes = nodes_of(req)
    points = [(n.lat, n.lng) for n in nodes]
    matrix = await osrm_matrix(osrm_url, points, osrm_timeout_s) if osrm_url else None
    source = "osrm"
    if matrix is None:
        matrix, source = haversine_matrix(points), "haversine"
    inst = Instance(nodes, matrix[0], matrix[1], req)
    return await asyncio.to_thread(plan, inst, req.time_limit_s, source)
