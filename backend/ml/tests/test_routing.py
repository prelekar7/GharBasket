"""Van routing (M10 §3.4): two waves on a synthetic Kharghar morning, the checker, the
haversine matrix, the nearest-neighbour fallback, the endpoint. OSRM is not needed."""

import random

import httpx2
import pytest
from gharbasket_ml import routing
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import RouteRequest, StopIn
from gharbasket_ml.settings import Settings
from pydantic import SecretStr

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
    assert any("missing" in p for p in routing.check(inst, missing).problems)


def test_nearest_neighbour_is_a_valid_fallback() -> None:
    req = request(n=8)
    inst = instance(req)
    trips = routing.nearest_neighbour(inst)
    checked = routing.check(inst, trips)
    assert "missing" not in " ".join(checked.problems)
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


@pytest.mark.anyio
async def test_the_osrm_table_is_read_or_left_alone() -> None:
    def table(request: httpx2.Request) -> httpx2.Response:
        n = request.url.path.count(";") + 1
        return httpx2.Response(
            200,
            json={
                "code": "Ok",
                "distances": [[0 if i == j else 1000 for j in range(n)] for i in range(n)],
                "durations": [[0 if i == j else 120 for j in range(n)] for i in range(n)],
            },
        )

    def down(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    points = [HUB, BANK, (19.04, 73.07)]
    # The matrix helper opens its own client; monkeypatch the transport through httpx2.
    original = httpx2.AsyncClient

    class Patched(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(table)
            super().__init__(*args, **kwargs)

    httpx2.AsyncClient = Patched  # type: ignore[assignment]
    try:
        matrix = await routing.osrm_matrix("http://osrm", points)
    finally:
        httpx2.AsyncClient = original  # type: ignore[assignment]
    assert matrix is not None and matrix[0][0][1] == 1000 and matrix[1][1][2] == 120

    class Broken(original):  # type: ignore[misc]
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(down)
            super().__init__(*args, **kwargs)

    httpx2.AsyncClient = Broken  # type: ignore[assignment]
    try:
        assert await routing.osrm_matrix("http://osrm", points) is None
    finally:
        httpx2.AsyncClient = original  # type: ignore[assignment]
