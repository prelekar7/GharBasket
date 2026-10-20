"""M7's own route arithmetic (spec §3.2): the fallback orders (previous plan, nearest neighbour),
trips filled to each vehicle's capacity, and the structural check."""

import uuid

from gharbasket.modules.logistics import routing
from gharbasket.modules.logistics.routing import Stop, Trip, Van

HUB = (19.0330, 73.0297)
SEVEN, NINE, ELEVEN = 7 * 3600, 9 * 3600, 11 * 3600


def stop(n: int, point: tuple[float, float], *, early: bool = True, crates: int = 4) -> Stop:
    society = uuid.UUID(int=n)
    window = (SEVEN, NINE) if early else (NINE, ELEVEN)
    return Stop(f"s{n}", society, uuid.UUID(int=100 + n), point, window, crates * 3, crates, 600)


# North-east of the hub, nearest first: s1, s2, s3; s4 is far south.
S1 = stop(1, (19.0400, 73.0350))
S2 = stop(2, (19.0450, 73.0400))
S3 = stop(3, (19.0500, 73.0450))
S4 = stop(4, (19.0000, 73.1200))
LATE = stop(5, (19.0410, 73.0360), early=False)
VANS = [Van("EV-1", 400, 10), Van("EV-2", 400, 10)]


def test_nearest_neighbour_takes_early_windows_first_then_the_nearest() -> None:
    order = routing.order_nearest([LATE, S4, S3, S1, S2], HUB)
    assert [s.id for s in order] == ["s1", "s2", "s3", "s4", "s5"]


def test_the_previous_order_is_kept_and_new_societies_follow_their_nearest_neighbour() -> None:
    # Yesterday: s3, s1, s4 (in that order). s2 is new: nearest to s1 (and s3); it follows s1
    # only if s1 is nearer, which it is not: s2 sits between s1 and s3, nearer s3.
    previous = [S3.society_id, S1.society_id, S4.society_id]
    order = routing.order_previous([S1, S2, S3, S4, LATE], previous)
    near = min((S1, S3), key=lambda k: routing.metres(k.point, S2.point))
    expected = ["s3", "s1", "s4"]
    expected.insert(expected.index(near.id) + 1, "s2")
    assert [s.id for s in order] == [*expected, "s5"]  # the late window last
    # With no previous plan at all, a new society sorts after every known one.
    assert [s.id for s in routing.order_previous([S2, S1], [])] == ["s1", "s2"]


def test_trips_fill_each_vehicle_to_capacity_and_serve_every_stop() -> None:
    sequence = routing.order_nearest([S1, S2, S3, S4, LATE], HUB)
    trips = routing.fill(sequence, VANS, HUB, depart_s=6 * 3600 + 1800)
    stops = {s.id: s for s in sequence}

    # 10 crates a van, 4 a stop: two stops a trip.
    assert [t.stops for t in trips] == [["s1", "s2"], ["s3", "s4"], ["s5"]]
    assert [t.van for t in trips] == [0, 1, 0]  # EV-1 goes out again when it is back first
    assert routing.check(trips, stops, VANS, depart_s=23_400, return_by_s=ELEVEN + 5400) == []
    first = trips[0]
    # Leaves just in time for 07:00, never before 06:30; waits at no stop after the first.
    assert first.start_s >= 6 * 3600 + 1800 and first.etas[0] == SEVEN
    assert first.etas[1] > first.etas[0] and first.end_s > first.etas[1]
    assert trips[2].start_s >= first.end_s + routing.RELOAD_S
    numbered = [(van, n) for van, n, _ in routing.numbered(trips)]
    assert numbered == [(0, 1), (0, 2), (1, 1)]
    figures = routing.summary(trips, stops)
    assert figures["on_time"] == 1.0 and figures["late_min"] == 0.0
    assert figures["km"] == round(sum(t.metres for t in trips) / 1000, 2)
    assert figures["cost_paise"] > 2 * routing.FIXED_PAISE_PER_VAN


def test_a_stop_bigger_than_any_vehicle_rides_alone_and_is_flagged() -> None:
    huge = stop(6, (19.0420, 73.0380), crates=12)
    trips = routing.fill([huge, S1], VANS, HUB, depart_s=23_400)
    assert [t.stops for t in trips] == [["s6"], ["s1"]]
    problems = routing.check(trips, {"s6": huge, "s1": S1}, VANS, 23_400, ELEVEN)
    assert problems == ["EV-1 trip 1: over capacity leaving the hub"]


def test_the_check_names_missing_stops_extra_or_early_trips_and_late_returns() -> None:
    stops = {s.id: s for s in (S1, S2, S3)}
    trips = [
        Trip(0, 25_000, ["s1"], [25_200], end_s=27_000),  # before the vans are loaded
        Trip(0, 28_000, ["s1"], [28_200], end_s=29_000),  # before trip 1 is back and reloaded
        Trip(0, 31_000, [], [], end_s=40_000),
    ]
    assert routing.check(trips, stops, VANS, depart_s=25_500, return_by_s=36_000) == [
        "EV-1 trip 1: leaves before 07:05",
        "EV-1 trip 2: leaves before trip 1 is back",
        "EV-1 trip 3: back after 10:00",
        "EV-1: 3 trips, more than 2",
        "not served: s2, s3",
        "visited twice: s1",
    ]
    assert routing.summary([], {})["on_time"] == 1.0
    assert routing.clock(-5) == "00:00" and routing.clock(90_000) == "23:59"
