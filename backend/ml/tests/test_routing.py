"""Van routing (M10 §3.4): two waves on a synthetic Kharghar morning, the checker, the
haversine matrix, the nearest-neighbour fallback, the endpoint. OSRM is not needed."""

import asyncio
import contextlib
import random
import time
from collections.abc import AsyncIterator, Callable

import httpx2
import pytest
from gharbasket_ml import routing
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import RouteRequest, StopIn
from gharbasket_ml.settings import Settings
from pydantic import SecretStr, ValidationError

TOKEN = "ml-test-token"
HUB = (19.0300, 73.0690)
BANK = (19.0190, 73.0390)
T = lambda h, m=0: int(h * 3600 + m * 60)  # noqa: E731 (seconds after midnight IST)


def request(n: int = 12, seed: int = 7, donations: int = 2, **overrides) -> RouteRequest:
    rng = random.Random(seed)
    stops = []
    for i in range(n):
        orders = rng.randint(4, 20)
        stops.append(
            StopIn(
                id=f"society-{i}",
                lat=rng.uniform(18.995, 19.055),
                lng=rng.uniform(73.035, 73.125),
                orders=orders,
                kg_tenths=orders * rng.randint(30, 60),
                crates=-(-orders // 3),
                empties=rng.randint(0, 6),
                donation_kg_tenths=rng.randint(20, 60) if i < donations else 0,
                window_start_s=T(7) if i % 2 == 0 else T(8),
                window_end_s=T(9) if i % 2 == 0 else T(10),
            )
        )
    body = {
        "hub": {"lat": HUB[0], "lng": HUB[1]},
        "depart_s": T(6, 30),
        "return_by_s": T(11, 30),
        "stops": [s.model_dump() for s in stops],
        "food_bank": {
            "lat": BANK[0],
            "lng": BANK[1],
            "window_start_s": T(8),
            "window_end_s": T(12),
        },
        "vans": {"count": 6},
        "time_limit_s": 2.0,
        **overrides,
    }
    return RouteRequest.model_validate(body)


def instance(req: RouteRequest) -> routing.Instance:
    nodes = routing.nodes_of(req)
    dist, dur = routing.haversine_matrix([(n.lat, n.lng) for n in nodes])
    return routing.Instance(nodes, dist, dur, req)


def test_the_haversine_matrix_is_symmetric_with_a_detour() -> None:
    dist, dur = routing.haversine_matrix([HUB, BANK, (19.0395, 73.0675)])
    assert dist[0][0] == 0 and dist[0][1] == dist[1][0] > 3000
    assert dur[0][1] == round(dist[0][1] / routing.HAVERSINE_SPEED_MPS)
    assert abs(routing.haversine_m(HUB, BANK) * routing.DETOUR - dist[0][1]) < 1


def test_two_waves_serve_every_society_on_time_within_capacity() -> None:
    req = request()
    inst = instance(req)

    trips = routing.waves(inst, req.time_limit_s)

    assert trips is not None
    checked = routing.check(inst, trips)
    assert checked.problems == [] and checked.on_time == 1.0
    assert 1 <= checked.vans <= 6 and checked.km > 0 and checked.cost_paise > 0
    served = sorted(i for t in trips for i in t.stops if inst.nodes[i].kind == "drop")
    assert served == [i for i, n in enumerate(inst.nodes) if n.kind == "drop"]
    # The second wave reuses a first-wave van: two trips share a van number.
    vans = [t.van for t in trips if t.stops]
    assert len(vans) > len(set(vans)) or checked.vans <= 3


def test_the_checker_catches_a_bad_plan() -> None:
    req = request(n=6, donations=1)
    inst = instance(req)
    drops = [i for i, n in enumerate(inst.nodes) if n.kind == "drop"]
    bank = next(i for i, n in enumerate(inst.nodes) if n.kind == "bank")

    everything_on_one_van_too_late = [routing.Trip(0, T(10, 30), [*drops, bank])]
    bank_first = [routing.Trip(0, T(6, 30), [bank, *drops])]
    missing = [routing.Trip(0, T(6, 30), [*drops[:-1], bank])]

    late = routing.check(inst, everything_on_one_van_too_late)
    assert late.on_time < 1 and any("back after" in p or "capacity" in p for p in late.problems)
    assert any("food bank before" in p for p in routing.check(inst, bank_first).problems)
    assert f"not served: {inst.nodes[drops[-1]].id}" in routing.check(inst, missing).problems


def test_nearest_neighbour_is_a_valid_fallback() -> None:
    req = request(n=8)
    inst = instance(req)
    trips = routing.nearest_neighbour(inst)
    checked = routing.check(inst, trips)
    assert "not served" not in " ".join(checked.problems)
    assert all("capacity" not in p for p in checked.problems)


@pytest.mark.anyio
async def test_the_endpoint_answers_with_haversine_when_osrm_is_unset() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = request(n=5, donations=0).model_dump()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        ok = await c.post("/route/solve", json=body, headers={"Authorization": f"Bearer {TOKEN}"})
        no = await c.post("/route/solve", json=body)
    assert ok.status_code == 200, ok.text
    answer = ok.json()
    assert answer["source"] == "haversine" and answer["status"] == "solved"
    assert answer["summary"]["problems"] == [] and answer["summary"]["on_time"] == 1.0
    assert sum(len(r["stops"]) for r in answer["routes"]) == 5
    assert all(s["eta_s"] >= T(7) for r in answer["routes"] for s in r["stops"])
    assert no.status_code == 401


@contextlib.contextmanager
def osrm_answering(handler: Callable[[httpx2.Request], httpx2.Response]):
    """The matrix helper opens its own client: hand it a mock transport through httpx2."""
    original = httpx2.AsyncClient

    class Patched(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(handler)
            super().__init__(*args, **kwargs)

    httpx2.AsyncClient = Patched  # type: ignore[assignment]
    try:
        yield
    finally:
        httpx2.AsyncClient = original  # type: ignore[assignment]


def osrm_table(gap: tuple[int, int] | None = None):
    def table(request: httpx2.Request) -> httpx2.Response:
        n = request.url.path.count(";") + 1
        dist: list[list[float | None]] = [
            [0 if i == j else 1000 for j in range(n)] for i in range(n)
        ]
        dur: list[list[float | None]] = [[0 if i == j else 120 for j in range(n)] for i in range(n)]
        if gap is not None:
            dist[gap[0]][gap[1]] = dur[gap[0]][gap[1]] = None
        return httpx2.Response(200, json={"code": "Ok", "distances": dist, "durations": dur})

    return table


@pytest.mark.anyio
async def test_the_osrm_table_is_read_or_left_alone() -> None:
    points = [HUB, BANK, (19.04, 73.07)]
    with osrm_answering(osrm_table()):
        matrix = await routing.osrm_matrix("http://osrm", points)
    assert matrix is not None and matrix[0][0][1] == 1000 and matrix[1][1][2] == 120

    with osrm_answering(lambda request: httpx2.Response(503)):
        assert await routing.osrm_matrix("http://osrm", points) is None
    with osrm_answering(lambda request: httpx2.Response(200, json={"code": "Ok"})):
        assert await routing.osrm_matrix("http://osrm", points) is None
    short = {"code": "Ok", "distances": [[0]], "durations": [[0]]}
    with osrm_answering(lambda request: httpx2.Response(200, json=short)):
        assert await routing.osrm_matrix("http://osrm", points) is None
    assert await routing.osrm_matrix("http://osrm", [HUB] * (routing.MAX_TABLE + 1)) is None


@pytest.mark.anyio
async def test_a_pair_osrm_cannot_route_takes_the_haversine_estimate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    points = [HUB, BANK, (19.04, 73.07)]
    with osrm_answering(osrm_table(gap=(0, 1))):
        matrix = await routing.osrm_matrix("http://osrm", points)
    assert matrix is not None
    dist, dur = matrix
    assert (dist[0][1], dur[0][1]) == routing.haversine_pair(HUB, BANK)
    assert dist[0][1] > 3000 and dist[1][0] == 1000 and dur[0][2] == 120
    assert "osrm_gaps: 1 of 9 pairs" in caplog.text


@contextlib.asynccontextmanager
async def hanging_osrm() -> AsyncIterator[str]:
    """A server that accepts the connection and never answers."""

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await reader.read()
        writer.close()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.close()
        server.close_clients()
        await server.wait_closed()


@pytest.mark.anyio
async def test_a_hanging_osrm_falls_back_to_haversine_within_the_budget() -> None:
    req = request(n=6, donations=1, time_limit_s=1.0)
    async with hanging_osrm() as url:
        started = time.perf_counter()
        assert await routing.osrm_matrix(url, [HUB, BANK], timeout_s=0.3) is None
        assert time.perf_counter() - started < 1.0

        started = time.perf_counter()
        answer = await routing.solve(req, url, osrm_timeout_s=0.3)
        took = time.perf_counter() - started
    assert answer.source == "haversine" and answer.summary.problems == []
    # The gateway allows time_limit_s + ML_ROUTE_OVERHEAD_S (6 s); the ML side stays well inside.
    assert took < 0.3 + req.time_limit_s + 2.0


# --- the fleet, the loads and the checker's other rules (review of PR #116) ---------------------


def test_the_second_wave_never_adds_more_vans_than_the_fleet() -> None:
    req = request(n=16, seed=5, vans={"count": 2})
    inst = instance(req)

    answer = routing.plan(inst, req.time_limit_s, "haversine")

    assert answer.summary.vans <= 2 and len({r.van for r in answer.routes}) <= 2
    assert all(r.van < 2 for r in answer.routes)
    assert not any("fleet" in p for p in answer.summary.problems)
    # Two vans cannot do this morning: whatever is left over is named, never a third van.
    served = {s.id for r in answer.routes for s in r.stops}
    if len(served) < len(inst.nodes) - 1:
        assert any(p.startswith("not served: ") for p in answer.summary.problems)


def test_the_fallback_keeps_to_the_fleet_and_names_what_it_leaves() -> None:
    req = request(n=40, vans={"count": 3})
    inst = instance(req)

    trips = routing.nearest_neighbour(inst)
    checked = routing.check(inst, trips)

    assert {t.van for t in trips} <= {0, 1, 2} and checked.vans == 3
    assert len(trips) > 3  # vans go out again once back and reloaded
    left = next(p for p in checked.problems if p.startswith("not served: "))
    served = {inst.nodes[i].id for t in trips for i in t.stops}
    assert all(name not in served for name in left.removeprefix("not served: ").split(", "))
    assert not any("capacity" in p or "fleet" in p or "before" in p for p in checked.problems)


def two_stops(**overrides) -> RouteRequest:
    stop = {"lat": 19.035, "lng": 73.075, "window_start_s": T(7), "window_end_s": T(9)}
    body = {
        "hub": {"lat": HUB[0], "lng": HUB[1]},
        "depart_s": T(6, 30),
        "return_by_s": T(11, 30),
        "stops": [
            {**stop, "id": "a", "crates": 5, "kg_tenths": 100},
            {**stop, "id": "b", "crates": 5, "kg_tenths": 100, "lng": 73.08},
        ],
        "vans": {"count": 2},
        "time_limit_s": 1.0,
    }
    return RouteRequest.model_validate({**body, **overrides})


def test_the_checker_weighs_the_load_leaving_the_hub() -> None:
    inst = instance(two_stops(vans={"count": 2, "capacity_crates": 9}))
    # 10 crates leave against 9: after the first drop only 5 are left on board.
    problems = routing.check(inst, [routing.Trip(0, T(6, 45), [1, 2])]).problems
    assert problems == ["van 0: over capacity leaving the hub"]
    assert (
        routing.check(
            inst, [routing.Trip(0, T(6, 45), [1]), routing.Trip(1, T(6, 45), [2])]
        ).problems
        == []
    )


def test_the_checker_counts_the_fleet_and_the_departure() -> None:
    inst = instance(two_stops(vans={"count": 1}))
    problems = routing.check(
        inst, [routing.Trip(0, T(6), [1]), routing.Trip(1, T(6, 45), [2])]
    ).problems
    assert "van 0: leaves before the vans are loaded" in problems
    assert "2 vans used, more vans than the fleet of 1" in problems


def test_the_checker_flags_a_very_late_drop_and_a_closed_food_bank() -> None:
    bank = {"lat": BANK[0], "lng": BANK[1], "window_start_s": T(7), "window_end_s": T(9, 30)}
    req = two_stops(food_bank=bank, max_late_s=600)
    req.stops[0].donation_kg_tenths = 30
    inst = instance(req)
    bank_node = next(i for i, n in enumerate(inst.nodes) if n.kind == "bank")

    on_time = routing.check(inst, [routing.Trip(0, T(6, 45), [1, 2, bank_node])])
    late = routing.check(inst, [routing.Trip(0, T(9, 15), [1, 2, bank_node])])

    assert on_time.problems == []
    assert any(p.startswith("a: ") and p.endswith(" min late") for p in late.problems)
    assert "van 0: reaches the food bank after it closes" in late.problems
    # Within the threshold, a late drop counts against on_time but is not a problem.
    lenient = instance(two_stops(max_late_s=7200))
    assert routing.check(lenient, [routing.Trip(0, T(9, 15), [1, 2])]).problems == []


def test_the_fallback_counts_the_empties_it_picks_up() -> None:
    # a leaves 4 crates and takes 8 empties: with b's 4 crates on board too, 12 > 10.
    req = two_stops(vans={"count": 2, "capacity_crates": 10})
    req.stops[0].crates, req.stops[0].empties = 4, 8
    req.stops[1].crates = 4
    inst = instance(req)

    trips = routing.nearest_neighbour(inst)

    assert sorted(len(t.stops) for t in trips) == [1, 1]
    assert routing.check(inst, trips).problems == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"food_bank": None}, "donations need a food_bank"),
        ({"depart_s": T(12)}, "depart_s must be before return_by_s"),
        ({"stop_window": (T(9), T(8))}, "window_start_s is after window_end_s"),
        ({"bank_window": (T(12), T(8))}, "window_start_s is after window_end_s"),
    ],
)
def test_an_inconsistent_request_is_refused(change: dict, message: str) -> None:
    body = request(n=3, donations=1).model_dump()
    if "stop_window" in change:
        body["stops"][0]["window_start_s"], body["stops"][0]["window_end_s"] = change.pop(
            "stop_window"
        )
    if "bank_window" in change:
        body["food_bank"]["window_start_s"], body["food_bank"]["window_end_s"] = change.pop(
            "bank_window"
        )
    with pytest.raises(ValidationError, match=message):
        RouteRequest.model_validate({**body, **change})


@pytest.mark.anyio
async def test_the_endpoint_answers_422_for_donations_without_a_food_bank() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = request(n=3, donations=1).model_dump() | {"food_bank": None}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        answer = await c.post(
            "/route/solve", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert answer.status_code == 422 and "food_bank" in answer.text
