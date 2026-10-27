"""Green-slot pricing (M10 §3.8 `POST /slots/price`, M-H): the marginal route cost of adding a
society's load to a day's tentative van routes, per delivery slot.

No solver and no randomness: a candidate joins its own society's stop in the slot, or is tried
at every position of every trip and as a new trip, and the cheapest position the checker
accepts wins, so the same request gives the same answer. The matrix, nodes, nearest neighbour
and checker are `routing`'s. The search replays only the van an insertion changes; the winner
is replayed on the whole day, and that replay is the answer. Route cost only: M3 turns a cost
into a saving, never this service.
"""

import asyncio
import time
from collections import Counter
from dataclasses import dataclass, replace

from gharbasket_ml import routing
from gharbasket_ml.routing import Instance, Node, Trip
from gharbasket_ml.schemas import (
    RouteRequest,
    SlotBaseOut,
    SlotCandidateIn,
    SlotCandidateOut,
    SlotIn,
    SlotPositionOut,
    SlotPriceOut,
    SlotPriceRequest,
    SlotPriceResponse,
)

MODEL = "slot-insertion-v0"
OSRM_TIMEOUT_S = 1.0  # the default of Settings.slot_osrm_timeout_s: a customer is waiting
_WHOLE_DAY = ("not served", "visited twice")  # rules a one-van replay cannot judge

Leg = tuple[int | None, int, list[int]]  # (index in the base's trips or None, van, node indexes)


@dataclass
class Spot:
    """Where the candidate goes: the day's legs with it, and the instance they run on."""

    inst: Instance
    legs: list[Leg]
    trip: int | None
    van: int
    after: str | None
    same_society: bool = False


def timed(inst: Instance, legs: list[Leg]) -> list[Trip]:
    """The legs with start times. A van leaves no earlier than depart_s, or than its previous
    trip's return plus reload_s, and just in time for the trip's earliest window, as
    `routing.nearest_neighbour` (and M7's fill) time a trip. A van's legs are in time order."""
    free: dict[int, int] = {}
    trips = []
    for _, van, stops in legs:
        first = min(inst.nodes[i].window[0] for i in stops)
        start = max(free.get(van, inst.req.depart_s), first - inst.travel_s(0, stops[0]))
        trip = Trip(van, start, stops)
        free[van] = routing.trip_end(inst, trip) + inst.req.vans.reload_s
        trips.append(trip)
    return trips


def replay_van(inst: Instance, legs: list[Leg]) -> tuple[routing.Checked, Counter[str]]:
    """One van's trips through the checker, with its problems that do not need the whole day."""
    checked = routing.check(inst, timed(inst, legs))
    return checked, Counter(p for p in checked.problems if not p.startswith(_WHOLE_DAY))


def totals(inst: Instance, legs: list[Leg]) -> tuple[int, int, int, routing.Checked]:
    """The whole day replayed: cost in paise, metres and van seconds."""
    trips = timed(inst, legs)
    checked = routing.check(inst, trips)
    seconds = sum(checked.ends[k] - t.start for k, t in enumerate(trips))
    return checked.cost_paise, round(checked.km * 1000), seconds, checked


def cheapest(inst: Instance, legs: list[Leg], c: int) -> Spot | None:
    """The candidate node `c` at every position of every trip, then as a new trip after each
    van's last trip and on the first unused van. The cheapest whose van replay adds no problem
    to the van's own wins; ties go to the earlier trip, then the earlier position."""
    req = inst.req
    cand = inst.nodes[c]
    limit = cand.window[1] + req.max_late_s
    cap_kg, cap_crates = req.vans.capacity_kg * 10, req.vans.capacity_crates
    by_van = {v: [leg for leg in legs if leg[1] == v] for v in range(req.vans.count)}
    base = {v: replay_van(inst, mine) for v, mine in by_van.items() if mine}
    best: tuple[int, Spot] | None = None

    def consider(mine: list[Leg], j: int, pos: int, old: Leg | None) -> bool:
        """The van's legs `mine` with the candidate at `pos` of leg j, which replaces `old` (None:
        a new trip). False when the candidate is too late here and at every later position."""
        nonlocal best
        _, van, stops = new = mine[j]
        checked, problems = replay_van(inst, mine)
        if pos and checked.etas[j, stops[pos - 1]] + inst.service_s(stops[pos - 1]) > limit:
            return False
        before = base.get(van)
        delta = checked.cost_paise - (before[0].cost_paise if before else 0)
        if not problems - (before[1] if before else Counter()) and (
            best is None or delta < best[0]
        ):
            day = [new if leg is old else leg for leg in legs] if old else [*legs, new]
            after = inst.nodes[stops[pos - 1]].id if pos else None
            best = delta, Spot(inst, day, new[0], van, after)
        return True

    for old in legs:
        t, van, stops = old
        if (
            sum(inst.nodes[i].kg for i in stops) + cand.kg > cap_kg
            or sum(inst.nodes[i].crates for i in stops) + cand.crates > cap_crates
        ):
            continue  # over capacity leaving the hub, wherever it goes
        mine = by_van[van]
        j = mine.index(old)
        for pos in range(len(stops) + 1):
            trial = [*mine[:j], (t, van, [*stops[:pos], c, *stops[pos:]]), *mine[j + 1 :]]
            if not consider(trial, j, pos, old):
                break
    used = [v for v in range(req.vans.count) if by_van[v]]
    unused = [v for v in range(req.vans.count) if not by_van[v]]
    for van in used + unused[:1]:
        mine = [*by_van[van], (None, van, [c])]
        consider(mine, len(mine) - 1, 0, None)
    return best[1] if best else None


def joined(inst: Instance, legs: list[Leg], cand: Node) -> Spot | None:
    """SAME_SOCIETY: the candidate's load on its society's stop in the slot (the stop whose id is
    the candidate node's). None without that stop, or when its van's replay adds a problem."""
    for t, van, stops in legs:
        for pos, i in enumerate(stops):
            if inst.nodes[i].id != cand.id:
                continue
            nodes = list(inst.nodes)
            nodes[i] = replace(
                nodes[i], kg=nodes[i].kg + cand.kg, crates=nodes[i].crates + cand.crates
            )
            heavier = Instance(nodes, inst.dist, inst.dur, inst.req)
            mine = [leg for leg in legs if leg[1] == van]
            if replay_van(heavier, mine)[1] - replay_van(inst, mine)[1]:
                return None
            after = inst.nodes[stops[pos - 1]].id if pos else None
            return Spot(heavier, legs, t, van, after, same_society=True)
    return None


def price_candidate(
    day: Instance, legs: list[Leg], c: int, cand: SlotCandidateIn, req: SlotPriceRequest
) -> list[SlotPriceOut]:
    """One candidate (node `c` of `day`) in every slot, priced alone against the base."""
    if cand.own_stops == "ignore":  # its society's stops leave the routes first
        legs = [
            (t, van, kept)
            for t, van, stops in legs
            if (kept := [i for i in stops if not day.nodes[i].id.startswith(f"{cand.id}/")])
        ]
    base_cost, base_m, base_s, _ = totals(day, legs)
    rows: list[dict] = []
    for slot in req.slots:
        window = (slot.window_start_s, slot.window_end_s)
        node = Node(
            f"{cand.id}/{slot.id}",
            cand.lat,
            cand.lng,
            "drop",
            cand.kg_tenths,
            cand.crates,
            window=window,
        )
        nodes = list(day.nodes)
        nodes[c] = node
        inst = Instance(nodes, day.dist, day.dur, day.req)
        spot = (joined(inst, legs, node) if cand.own_stops == "join" else None) or cheapest(
            inst, legs, c
        )
        nearest = min(
            (
                min(inst.dist[c][i], inst.dist[i][c])
                for _, _, stops in legs
                for i in stops
                if inst.nodes[i].id.endswith(f"/{slot.id}")
            ),
            default=None,
        )
        row: dict = {"id": slot.id, "nearest_stop_m": nearest, "slot": slot}
        if spot is None:
            row |= {"feasible": False, "reason": "INFEASIBLE", "position": None}
            row |= dict.fromkeys(("marginal_cost_paise", "marginal_m", "marginal_s"))
        else:
            cost, metres, seconds, _ = totals(spot.inst, spot.legs)
            if spot.same_society:
                reason = "SAME_SOCIETY"
            elif nearest is not None and nearest <= req.near_m:
                reason = "NEIGHBOUR_STOP"
            else:
                reason = "NEW_TRIP" if spot.trip is None else "NEW_STOP"
            row |= {
                "feasible": True,
                "reason": reason,
                "marginal_cost_paise": max(0, cost - base_cost),
                "marginal_m": max(0, metres - base_m),
                "marginal_s": max(0, seconds - base_s),
                "position": SlotPositionOut(trip=spot.trip, van=spot.van, after=spot.after),
            }
        rows.append(row)
    return rank(rows)


def rank(rows: list[dict]) -> list[SlotPriceOut]:
    """Feasible slots by marginal cost, then window start, then slot id; infeasible ones after
    them by window start (then id). The rows stay in the request's slot order."""

    def key(row: dict) -> tuple:
        slot: SlotIn = row["slot"]
        cost = row["marginal_cost_paise"] if row["feasible"] else 0
        return (not row["feasible"], cost, slot.window_start_s, slot.id)

    ordered = sorted(range(len(rows)), key=lambda k: key(rows[k]))
    ranks = {k: r for r, k in enumerate(ordered, start=1)}
    return [
        SlotPriceOut(rank=ranks[k], **{f: v for f, v in row.items() if f != "slot"})
        for k, row in enumerate(rows)
    ]


def price_day(
    req: SlotPriceRequest, dist: list[list[int]], dur: list[list[int]], source: str
) -> SlotPriceResponse:
    """The base (the given trips, else nearest neighbour) and every candidate against it. Nodes:
    the hub, the stops, then one per candidate (index len(stops) + 1 + k), as in the matrix."""
    started = time.perf_counter()
    rr = RouteRequest.model_construct(  # an empty day has no stops, which RouteRequest refuses
        hub=req.hub,
        depart_s=req.depart_s,
        return_by_s=req.return_by_s,
        stops=req.stops,
        food_bank=None,
        vans=req.vans,
        service=req.service,
        cost=req.cost,
        max_late_s=req.max_late_s,
    )
    stops = routing.nodes_of(rr)
    base = Instance(stops, dist, dur, rr)
    if req.trips is None:
        legs: list[Leg] = [
            (k, t.van, t.stops) for k, t in enumerate(routing.nearest_neighbour(base))
        ]
    else:
        index = {nd.id: i for i, nd in enumerate(stops) if i}
        legs = [(k, t.van, [index[s] for s in t.stops]) for k, t in enumerate(req.trips)]
    day = Instance(
        [*stops, *(Node(c.id, c.lat, c.lng, "drop") for c in req.candidates)], dist, dur, rr
    )
    *_, checked = totals(base, legs)
    return SlotPriceResponse(
        source=source,  # pyright: ignore[reportArgumentType]
        base=SlotBaseOut(
            trips=len(legs),
            vans=checked.vans,
            km=round(checked.km, 2),
            cost_paise=checked.cost_paise,
            problems=checked.problems,
        ),
        candidates=[
            SlotCandidateOut(id=c.id, slots=price_candidate(day, legs, len(stops) + k, c, req))
            for k, c in enumerate(req.candidates)
        ],
        seconds=round(time.perf_counter() - started, 3),
        model=MODEL,
    )


async def price(
    req: SlotPriceRequest, osrm_url: str | None, osrm_timeout_s: float = OSRM_TIMEOUT_S
) -> SlotPriceResponse:
    """The endpoint: one matrix over the hub, the stops and the candidates (OSRM within
    `osrm_timeout_s`, else haversine), then the pricing on a worker thread."""
    points = [
        (req.hub.lat, req.hub.lng),
        *((s.lat, s.lng) for s in req.stops),
        *((c.lat, c.lng) for c in req.candidates),
    ]
    dist, dur, source = await routing.matrix(points, osrm_url, osrm_timeout_s)
    return await asyncio.to_thread(price_day, req, dist, dur, source)
