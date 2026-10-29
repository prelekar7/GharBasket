"""Route arithmetic in the API (spec §3.2): the fallback plan when the ML service does not answer,
and M7's structural check on every plan, from the ML service or not.

Pure functions on seconds since IST midnight. Distances are haversine x 1.3 and travel times
that distance at 22 km/h x the traffic factor, with the M10 defaults for service times, so a
fallback plan's ETAs are comparable with the ML service's haversine answers.
"""

import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

DETOUR = 1.3  # road distance over the straight line in a dense suburb (M10's)
SPEED_MPS = 6.0  # about 22 km/h door to door
TRAFFIC = 1.5
DROP_S = 240  # the handover at a society
PER_CRATE_S = 30
RELOAD_S = 1200  # back at the hub before a second trip
MAX_TRIPS = 2
# M10 RouteCost defaults, for a fallback plan's summary.
FIXED_PAISE_PER_VAN, PAISE_PER_KM, WAGE_PAISE_PER_S = 25_000, 60, 3

Point = tuple[float, float]


class Placed(Protocol):
    """What ordering and replaying need of a stop: a box drop (`Stop`) or a tiffin route's kitchen
    or drop (`tiffin_plans.Place`, §3.7)."""

    @property
    def id(self) -> str: ...
    @property
    def point(self) -> Point: ...
    @property
    def window(self) -> tuple[int, int]: ...
    @property
    def crates(self) -> int: ...


@dataclass(frozen=True)
class Stop:
    id: str  # "<society_id>/<slot_id>"
    society_id: uuid.UUID
    slot_id: uuid.UUID
    point: Point
    window: tuple[int, int]
    orders: int
    crates: int
    kg_tenths: int
    empties: int = 0  # crates to take back (§3.6); the fallback ignores them


@dataclass(frozen=True)
class Van:
    label: str
    capacity_kg: int
    capacity_crates: int


@dataclass
class Trip:
    van: int  # index into the fleet, by label
    start_s: int
    stops: list[str]
    etas: list[int] = field(default_factory=list)
    end_s: int = 0
    metres: int = 0


def metres(a: Point, b: Point) -> int:
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    )
    return round(2 * 6_371_000 * math.asin(math.sqrt(h)) * DETOUR)


def travel_s(a: Point, b: Point) -> int:
    return round(round(metres(a, b) / SPEED_MPS) * TRAFFIC)


def replay(trip: Trip, hub: Point, stops: Mapping[str, Placed]) -> Trip:
    """Fill in the trip's ETAs (waiting when early), its end back at the hub and its metres."""
    t, here, distance, etas = trip.start_s, hub, 0, []
    for stop_id in trip.stops:
        stop = stops[stop_id]
        t += travel_s(here, stop.point)
        distance += metres(here, stop.point)
        t = max(t, stop.window[0])
        etas.append(t)
        t += DROP_S + PER_CRATE_S * stop.crates
        here = stop.point
    trip.etas, trip.end_s = etas, t + travel_s(here, hub)
    trip.metres = distance + metres(here, hub)
    return trip


def _nearest_chain[S: Placed](group: list[S], start: Point) -> list[S]:
    chain: list[S] = []
    here, todo = start, list(group)
    while todo:
        nearest = min(todo, key=lambda s: (metres(here, s.point), s.id))
        todo.remove(nearest)
        chain.append(nearest)
        here = nearest.point
    return chain


def order_nearest[S: Placed](stops: Sequence[S], hub: Point) -> list[S]:
    """`fallback_nearest`: early-window stops first, each window's stops by nearest neighbour
    from the hub (a tiffin trip's drops: from its last kitchen, §3.7)."""
    windows = sorted({s.window[0] for s in stops})
    return [s for w in windows for s in _nearest_chain([x for x in stops if x.window[0] == w], hub)]


def order_previous(stops: Sequence[Stop], previous: Sequence[uuid.UUID]) -> list[Stop]:
    """`fallback_previous`: early-window stops first, each window's stops in the order their
    societies had on the previous plan; a society that was not on it goes right after its
    nearest neighbour that was."""
    rank = {society: float(i) for i, society in reversed(list(enumerate(previous)))}
    known = [s for s in stops if s.society_id in rank]

    def position(stop: Stop) -> float:
        if stop.society_id in rank:
            return rank[stop.society_id]
        if not known:
            return math.inf
        nearest = min(known, key=lambda k: (metres(k.point, stop.point), k.id))
        return rank[nearest.society_id] + 0.5

    return sorted(stops, key=lambda s: (s.window[0], position(s), s.id))


def fill(sequence: Sequence[Stop], fleet: Sequence[Van], hub: Point, depart_s: int) -> list[Trip]:
    """Trips in the sequence's order, each filled up to its vehicle's kg and crates; a trip goes
    to the vehicle free first (a vehicle with fewer than two trips when there is one), leaving
    just in time for its first window."""
    stops = {s.id: s for s in sequence}
    free_at = [depart_s] * len(fleet)
    trips_of = [0] * len(fleet)
    trips: list[Trip] = []
    todo = list(sequence)
    while todo:
        open_vans = [v for v in range(len(fleet)) if trips_of[v] < MAX_TRIPS] or list(
            range(len(fleet))
        )
        van = min(open_vans, key=lambda v: (free_at[v], v))
        kg = crates = 0
        taken: list[Stop] = []
        while todo:
            stop = todo[0]
            kg_after, crates_after = kg + stop.kg_tenths, crates + stop.crates
            too_much = kg_after > fleet[van].capacity_kg * 10
            if taken and (too_much or crates_after > fleet[van].capacity_crates):
                break
            taken.append(todo.pop(0))
            kg, crates = kg_after, crates_after
        first = taken[0]
        start = max(free_at[van], first.window[0] - travel_s(hub, first.point))
        trip = replay(Trip(van, start, [s.id for s in taken]), hub, stops)
        trips.append(trip)
        trips_of[van] += 1
        free_at[van] = trip.end_s + RELOAD_S
    return trips


def check(
    trips: Sequence[Trip],
    stops: dict[str, Stop],
    fleet: Sequence[Van],
    depart_s: int,
    return_by_s: int,
) -> list[str]:
    """M7's structural check (spec §3.2): every stop on exactly one route, kg and crates within
    the vehicle's capacity leaving the hub, no more than two routes per vehicle, no route leaving
    before the vans are loaded (`depart_s`) or before the vehicle's previous trip is back and
    reloaded, and every route back by `return_by_s`."""
    problems: list[str] = []
    seen = [stop_id for trip in trips for stop_id in trip.stops]
    back_at: dict[int, int] = {}
    for van, trip_no, trip in numbered(trips):
        label = fleet[van].label
        if trip.start_s < depart_s:
            problems.append(f"{label} trip {trip_no}: leaves before {clock(depart_s)}")
        if van in back_at and trip.start_s < back_at[van] + RELOAD_S:
            problems.append(f"{label} trip {trip_no}: leaves before trip {trip_no - 1} is back")
        back_at[van] = trip.end_s
        kg = sum(stops[s].kg_tenths for s in trip.stops if s in stops)
        crates = sum(stops[s].crates for s in trip.stops if s in stops)
        if kg > fleet[van].capacity_kg * 10 or crates > fleet[van].capacity_crates:
            problems.append(f"{label} trip {trip_no}: over capacity leaving the hub")
        if trip.end_s > return_by_s:
            problems.append(f"{label} trip {trip_no}: back after {clock(return_by_s)}")
    for van in sorted({t.van for t in trips}):
        count = sum(1 for t in trips if t.van == van)
        if count > MAX_TRIPS:
            problems.append(f"{fleet[van].label}: {count} trips, more than {MAX_TRIPS}")
    missing = sorted(set(stops) - set(seen))
    if missing:
        problems.append("not served: " + ", ".join(missing))
    twice = sorted({s for s in seen if seen.count(s) > 1})
    if twice:
        problems.append("visited twice: " + ", ".join(twice))
    return problems


def numbered(trips: Sequence[Trip]) -> list[tuple[int, int, Trip]]:
    """(van, trip number, trip), a van's trips numbered by start time."""
    ordered = sorted(trips, key=lambda t: (t.van, t.start_s))
    out, count = [], {}
    for trip in ordered:
        count[trip.van] = count.get(trip.van, 0) + 1
        out.append((trip.van, count[trip.van], trip))
    return out


def summary(trips: Sequence[Trip], stops: Mapping[str, Placed]) -> dict[str, float | int]:
    """km, cost, on-time share and lateness of a fallback plan, as the ML service reports them."""
    late = on_time = drops = 0
    cost = 0.0
    for trip in trips:
        for stop_id, eta in zip(trip.stops, trip.etas, strict=True):
            drops += 1
            overdue = eta - stops[stop_id].window[1]
            on_time += overdue <= 0
            late += max(0, overdue)
        cost += trip.metres / 1000 * PAISE_PER_KM + (trip.end_s - trip.start_s) * WAGE_PAISE_PER_S
    cost += len({t.van for t in trips}) * FIXED_PAISE_PER_VAN
    return {
        "km": round(sum(t.metres for t in trips) / 1000, 2),
        "cost_paise": round(cost),
        "on_time": round(on_time / drops, 3) if drops else 1.0,
        "late_min": round(late / 60, 1),
    }


def clock(seconds: int) -> str:
    """Seconds since IST midnight as `HH:MM` (capped at 23:59)."""
    seconds = min(max(seconds, 0), 24 * 3600 - 60)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"
