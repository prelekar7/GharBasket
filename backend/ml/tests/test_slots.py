"""Green-slot pricing (M10 §3.8 `POST /slots/price`): the reasons, the costs against a full
replay, the ranks, determinism, the matrix fallbacks, the limits and the speed. OSRM is not
needed; distances are haversine x 1.3 unless a test fakes OSRM."""

import copy
import random
import time
from collections import Counter

import httpx2
import pytest
from gharbasket_ml import routing, slots
from gharbasket_ml.main import create_app
from gharbasket_ml.routing import Instance, Node
from gharbasket_ml.schemas import RouteRequest, SlotIn, SlotPriceRequest, SlotPriceResponse
from gharbasket_ml.settings import Settings
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import SecretStr

from tests.test_routing import hanging_osrm, osrm_answering, osrm_table

TOKEN = "ml-test-token"
LAT, LNG = 19.0300, 73.0690  # the hub
T = lambda h, m=0: int(h * 3600 + m * 60)  # noqa: E731 (seconds after midnight IST)
S1 = {"id": "s1", "window_start_s": T(8), "window_end_s": T(10)}
S2 = {"id": "s2", "window_start_s": T(10), "window_end_s": T(12)}


def stop(sid: str, dlat: float, dlng: float = 0.0, boxes: int = 3, slot: dict = S1) -> dict:
    return {
        "id": sid,
        "lat": LAT + dlat,
        "lng": LNG + dlng,
        "orders": boxes,
        "kg_tenths": boxes * 80,
        "crates": -(-boxes // 3),
        "window_start_s": slot["window_start_s"],
        "window_end_s": slot["window_end_s"],
    }


def cand(cid: str, dlat: float, dlng: float = 0.0, **more) -> dict:
    return {"id": cid, "lat": LAT + dlat, "lng": LNG + dlng, **more}


def body(stops=(), trips=None, candidates=(), slot_list=(S1,), **more) -> dict:
    return {
        "hub": {"lat": LAT, "lng": LNG},
        "depart_s": T(6, 30),
        "return_by_s": T(13),
        "stops": list(stops),
        "trips": trips,
        "vans": {"count": 2},
        "candidates": list(candidates),
        "slots": list(slot_list),
        **more,
    }


def points(req: SlotPriceRequest) -> list[tuple[float, float]]:
    return [
        (req.hub.lat, req.hub.lng),
        *((s.lat, s.lng) for s in req.stops),
        *((c.lat, c.lng) for c in req.candidates),
    ]


def run(data: dict) -> SlotPriceResponse:
    req = SlotPriceRequest.model_validate(data)
    dist, dur = routing.haversine_matrix(points(req))
    return slots.price_day(req, dist, dur, "haversine")


def priced(data: dict, k: int = 0) -> dict[str, dict]:
    return {s.id: s.model_dump() for s in run(data).candidates[k].slots}


def road_m(a: dict, b: dict) -> int:
    return routing.haversine_pair((a["lat"], a["lng"]), (b["lat"], b["lng"]))[0]


A, B = stop("a/s1", 0.02), stop("b/s1", 0.025)


# --- the reasons -------------------------------------------------------------------------------


def test_joining_its_own_societys_stop_costs_the_extra_crate_handover() -> None:
    data = body([A, B], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [cand("a", 0.02)])
    s1 = priced(data)["s1"]
    # One more crate at a/s1: per_crate_s (30 s) on the van's clock, x wage (3 paise/s); no metres.
    assert s1["reason"] == "SAME_SOCIETY" and s1["feasible"] and s1["rank"] == 1
    assert (s1["marginal_cost_paise"], s1["marginal_m"], s1["marginal_s"]) == (90, 0, 30)
    assert s1["nearest_stop_m"] == 0
    assert s1["position"] == {"trip": 0, "van": 0, "after": None}


def test_a_full_own_stop_is_priced_as_a_separate_drop() -> None:
    full = stop("a/s1", 0.02, boxes=24)  # 8 crates
    data = body([full], [{"van": 0, "stops": ["a/s1"]}], [cand("a", 0.02)])
    data["vans"] = {"count": 1, "capacity_crates": 8}
    s1 = priced(data)["s1"]
    assert s1["reason"] == "NEIGHBOUR_STOP" and s1["position"]["trip"] is None


def test_a_society_on_the_way_adds_almost_no_metres() -> None:
    a, b = stop("a/s1", 0.02), stop("b/s1", 0.02, 0.02)
    data = body([a, b], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [cand("m", 0.02, 0.01)])
    s1 = priced(data)["s1"]
    assert s1["position"] == {"trip": 0, "van": 0, "after": "a/s1"}
    assert s1["marginal_m"] <= 10 and s1["reason"] == "NEW_STOP"  # 1.37 km by road from each
    assert 0 < s1["marginal_cost_paise"] < 1500


def test_a_far_society_is_a_new_stop_with_a_real_cost() -> None:
    data = body([A, B], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [cand("far", 0.06)])
    s1 = priced(data)["s1"]
    assert s1["reason"] == "NEW_STOP" and s1["position"]["trip"] == 0
    assert s1["nearest_stop_m"] > 1000 and s1["marginal_m"] > 5000
    assert s1["marginal_cost_paise"] > 300


def test_an_empty_day_is_a_new_trip_in_every_slot() -> None:
    answer = run(body([], None, [cand("x", 0.01)], (S1, S2)))
    assert answer.base.model_dump() == {
        "trips": 0,
        "vans": 0,
        "km": 0.0,
        "cost_paise": 0,
        "problems": [],
    }
    for s in answer.candidates[0].slots:
        assert s.reason == "NEW_TRIP" and s.nearest_stop_m is None
        assert s.position is not None and s.position.model_dump() == {
            "trip": None,
            "van": 0,
            "after": None,
        }
        assert s.marginal_cost_paise is not None and s.marginal_cost_paise > 25_000  # a van
    # Same cost in both slots: the earlier window ranks first.
    assert [s.rank for s in answer.candidates[0].slots] == [1, 2]


@pytest.mark.parametrize(
    ("count", "return_by", "reason", "van", "with_van"),
    [
        (1, T(13), "NEW_TRIP", 0, False),  # the same van's second trip
        (2, T(8, 40), "NEW_TRIP", 1, True),  # no time for a second trip: the spare van
        (1, T(8, 40), "INFEASIBLE", None, False),  # and without a spare van, nothing
    ],
)
def test_a_full_van_needs_a_new_trip_a_new_van_or_nothing(
    count: int, return_by: int, reason: str, van: int | None, with_van: bool
) -> None:
    full = stop("a/s1", 0.02, boxes=9)  # 3 crates
    data = body([full], [{"van": 0, "stops": ["a/s1"]}], [cand("x", -0.02)])
    data |= {"vans": {"count": count, "capacity_crates": 3}, "return_by_s": return_by}
    s1 = priced(data)["s1"]
    assert s1["reason"] == reason
    if reason == "INFEASIBLE":
        assert s1["position"] is None and s1["marginal_cost_paise"] is None
        assert s1["marginal_m"] is None and s1["marginal_s"] is None
        return
    assert s1["position"] == {"trip": None, "van": van, "after": None}
    assert (s1["marginal_cost_paise"] >= 25_000) is with_van


def test_a_window_the_routes_cannot_meet_is_infeasible_and_ranked_last() -> None:
    dawn = {"id": "dawn", "window_start_s": T(5), "window_end_s": T(5, 30)}
    data = body([A], [{"van": 0, "stops": ["a/s1"]}], [cand("x", 0.01)], (dawn, S1))
    data["max_late_s"] = 0
    out = priced(data)
    assert out["dawn"]["reason"] == "INFEASIBLE" and out["dawn"]["rank"] == 2
    assert out["dawn"]["nearest_stop_m"] is None  # no stop in that slot
    assert out["s1"]["feasible"] and out["s1"]["rank"] == 1


def test_a_neighbour_is_a_stop_of_that_slot_within_near_m_by_road() -> None:
    near = cand("n", 0.026, 0.002)
    d = road_m(near, B)
    assert d < road_m(near, A)
    data = body([A, B], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [near], near_m=d)
    assert priced(data)["s1"]["reason"] == "NEIGHBOUR_STOP"
    assert priced(data)["s1"]["nearest_stop_m"] == d
    assert priced(data | {"near_m": d - 1})["s1"]["reason"] == "NEW_STOP"
    # A stop of another slot is not a neighbour in this one.
    other = stop("b/s2", 0.025, slot=S2)
    data = body([A, other], [{"van": 0, "stops": ["a/s1", "b/s2"]}], [near], near_m=d)
    assert priced(data)["s1"]["nearest_stop_m"] == road_m(near, A) > d


def test_ignore_prices_against_the_routes_without_the_societys_stops() -> None:
    c, b2 = stop("c/s1", 0.022, 0.004), stop("b/s2", 0.025, slot=S2)
    point = {"lat": c["lat"], "lng": c["lng"]}
    ignore = body(
        [A, c, b2],
        [{"van": 0, "stops": ["a/s1", "c/s1"]}, {"van": 1, "stops": ["b/s2"]}],
        [{"id": "c", **point, "own_stops": "ignore"}],
        (S1, S2),
    )
    without = body(
        [A, b2],
        [{"van": 0, "stops": ["a/s1"]}, {"van": 1, "stops": ["b/s2"]}],
        [{"id": "c", **point}],
        (S1, S2),
    )
    assert priced(ignore) == priced(without)
    assert priced(ignore)["s1"]["reason"] != "SAME_SOCIETY"


# --- costs against a full replay (hypothesis) --------------------------------------------------


def brute_force(req: SlotPriceRequest, slot: SlotIn) -> int | None:
    """Every position and every new trip, each replayed on the whole day: the least marginal
    cost among those that add no checker problem, or None."""
    dist, dur = routing.haversine_matrix(points(req))
    rr = RouteRequest.model_construct(**{**dict(req), "food_bank": None})
    stops = routing.nodes_of(rr)
    legs: list[slots.Leg] = [
        (k, t.van, t.stops)
        for k, t in enumerate(routing.nearest_neighbour(Instance(stops, dist, dur, rr)))
    ]
    c = req.candidates[0]
    node = Node(f"{c.id}/{slot.id}", c.lat, c.lng, "drop", c.kg_tenths, c.crates)
    node.window = (slot.window_start_s, slot.window_end_s)
    inst = Instance([*stops, node], dist, dur, rr)
    x = len(stops)

    def cost_and_problems(day: list[slots.Leg]) -> tuple[int, Counter[str]]:
        cost, _, _, checked = slots.totals(inst, day)
        return cost, Counter(p for p in checked.problems if not p.startswith(slots._WHOLE_DAY))

    base_cost, base_problems = cost_and_problems(legs)
    options = [
        [(t, v, [*s[:p], x, *s[p:]]) if k == j else (t, v, s) for k, (t, v, s) in enumerate(legs)]
        for j, (_, _, s0) in enumerate(legs)
        for p in range(len(s0) + 1)
    ] + [[*legs, (None, v, [x])] for v in range(req.vans.count)]
    costs = []
    for day in options:
        cost, problems = cost_and_problems(day)
        if not problems - base_problems:
            costs.append(max(0, cost - base_cost))
    return min(costs, default=None)


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(0, 10_000),
    n=st.integers(0, 7),
    count=st.integers(1, 3),
    crates=st.integers(2, 8),
    back=st.integers(T(9), T(13)),
)
def test_the_one_van_search_finds_the_full_replays_cheapest(
    seed: int, n: int, count: int, crates: int, back: int
) -> None:
    rng = random.Random(seed)
    stop_list = [
        stop(
            f"soc{i}/{'s1' if i % 2 else 's2'}",
            rng.uniform(-0.03, 0.03),
            rng.uniform(-0.03, 0.03),
            boxes=rng.randint(1, 9),
            slot=S1 if i % 2 else S2,
        )
        for i in range(n)
    ]
    data = body(stop_list, None, [cand("x", rng.uniform(-0.03, 0.03), rng.uniform(-0.03, 0.03))])
    # 1000 paise a km keeps every cost a whole paisa, so a van's difference and the full
    # replay's difference must agree exactly, not just within rounding.
    data |= {
        "slots": [S1, S2],
        "return_by_s": back,
        "vans": {"count": count, "capacity_crates": crates},
        "cost": {"paise_per_km": 1000},
    }
    req = SlotPriceRequest.model_validate(data)
    answer = run(data)
    for slot, out in zip(req.slots, answer.candidates[0].slots, strict=True):
        truth = brute_force(req, slot)
        assert out.feasible is (truth is not None)
        assert out.marginal_cost_paise == truth
        assert out.marginal_cost_paise is None or out.marginal_cost_paise >= 0


# --- ranks and determinism ---------------------------------------------------------------------


def test_the_rank_rules() -> None:
    def row(sid: str, start: int, cost: int | None) -> dict:
        slot = SlotIn(id=sid, window_start_s=start, window_end_s=start + 3600)
        return {
            "id": sid,
            "slot": slot,
            "feasible": cost is not None,
            "marginal_cost_paise": cost,
            "marginal_m": None,
            "marginal_s": None,
            "nearest_stop_m": None,
            "reason": "NEW_STOP" if cost is not None else "INFEASIBLE",
            "position": None,
        }

    rows = [
        row("z", T(8), 500),
        row("late-dead", T(11), None),
        row("b", T(9), 200),
        row("a", T(9), 200),  # same cost and start as b: the slot id decides
        row("early-dead", T(7), None),
        row("c", T(7), 200),  # same cost, earlier window
    ]
    ranked = slots.rank(rows)
    assert [r.id for r in ranked] == ["z", "late-dead", "b", "a", "early-dead", "c"]
    assert [r.rank for r in ranked] == [4, 6, 3, 2, 5, 1]


def kharghar(n: int, candidates: int, seed: int = 3, k_slots: int = 6) -> dict:
    rng = random.Random(seed)
    slot_list = [
        {"id": f"s{k}", "window_start_s": T(7 + k), "window_end_s": T(8 + k)}
        for k in range(k_slots)
    ]
    stop_list = [
        stop(
            f"soc{i}/s{i % k_slots}",
            rng.uniform(-0.035, 0.025),
            rng.uniform(-0.035, 0.055),
            boxes=rng.randint(2, 12),
            slot=slot_list[i % k_slots],
        )
        for i in range(n)
    ]
    cands = [
        cand(f"c{k}", rng.uniform(-0.035, 0.025), rng.uniform(-0.035, 0.055))
        for k in range(candidates)
    ]
    return body(stop_list, None, cands, slot_list, return_by_s=T(14), vans={"count": 6})


def without_seconds(answer: dict) -> dict:
    return {k: v for k, v in answer.items() if k != "seconds"}


def test_the_same_request_gives_the_same_answer_and_given_trips_match_null() -> None:
    data = kharghar(40, 5)
    first, second = run(data).model_dump(), run(data).model_dump()
    assert without_seconds(first) == without_seconds(second)
    # The nearest-neighbour trips, given back as trips, price the same.
    req = SlotPriceRequest.model_validate(data)
    rr = RouteRequest.model_construct(**{**dict(req), "food_bank": None})
    nodes = routing.nodes_of(rr)
    dist, dur = routing.haversine_matrix(points(req))
    trips = routing.nearest_neighbour(Instance(nodes, dist, dur, rr))
    given_trips = [{"van": t.van, "stops": [nodes[i].id for i in t.stops]} for t in trips]
    assert without_seconds(run(data | {"trips": given_trips}).model_dump()) == without_seconds(
        first
    )
    ranks = [sorted(s["rank"] for s in c["slots"]) for c in first["candidates"]]
    assert ranks == [list(range(1, 7))] * 5


# --- the matrix --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_osrm_answers_with_gaps_filled() -> None:
    req = SlotPriceRequest.model_validate(
        body([A, B], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [cand("x", 0.01)])
    )
    with osrm_answering(osrm_table(gap=(0, 3))):
        answer = await slots.price(req, "http://osrm")
    assert answer.source == "osrm" and answer.base.problems == []
    # The table says 1000 m between any two points; the hub → candidate gap is haversine.
    assert answer.candidates[0].slots[0].nearest_stop_m == 1000


@pytest.mark.anyio
async def test_a_slow_osrm_falls_back_to_haversine_within_the_budget() -> None:
    data = body([A, B], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [cand("x", 0.01)])
    async with hanging_osrm() as url:
        app = create_app(
            Settings(
                app_env="test",
                ml_service_token=SecretStr(TOKEN),
                osrm_url=url,
                slot_osrm_timeout_s=0.3,
            )
        )
        started = time.perf_counter()
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://ml"
        ) as c:
            answer = await c.post(
                "/slots/price", json=data, headers={"Authorization": f"Bearer {TOKEN}"}
            )
        took = time.perf_counter() - started
    assert answer.status_code == 200, answer.text
    assert answer.json()["source"] == "haversine" and took < 1.0


# --- the endpoint: limits, auth, speed ---------------------------------------------------------


def valid() -> dict:
    return body([A, B], [{"van": 0, "stops": ["a/s1", "b/s1"]}], [cand("x", 0.01)], (S1, S2))


def broken(change: str) -> dict:
    data = copy.deepcopy(valid())
    match change:
        case "stop window":
            data["stops"][0]["window_start_s"] = T(11)
        case "slot window":
            data["slots"][0] = {**S1, "window_start_s": T(11)}
        case "depart":
            data["depart_s"] = T(13)
        case "donations":
            data["stops"][0]["donation_kg_tenths"] = 20
        case "unknown stop":
            data["trips"][0]["stops"].append("nowhere/s1")
        case "stop twice":
            data["trips"].append({"van": 1, "stops": ["a/s1"]})
        case "stop on no trip":
            data["trips"][0]["stops"] = ["a/s1"]
        case "van":
            data["trips"][0]["van"] = 2
        case "duplicate candidates":
            data["candidates"] *= 2
        case "duplicate slots":
            data["slots"] = [S1, S1]
        case "duplicate stops":
            data["stops"].append(A)
        case "51 candidates":
            data["candidates"] = [cand(f"c{k}", 0.01) for k in range(51)]
        case "13 slots":
            data["slots"] = [{**S1, "id": f"s{k}"} for k in range(13)]
        case "no candidates":
            data["candidates"] = []
        case "near_m":
            data["near_m"] = 5001
    return data


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("stop window", "window_start_s is after window_end_s"),
        ("slot window", "slot s1: window_start_s is after window_end_s"),
        ("depart", "depart_s must be before return_by_s"),
        ("donations", "donations need a food_bank"),
        ("unknown stop", "unknown stops: nowhere/s1"),
        ("stop twice", "a stop is on more than one trip"),
        ("stop on no trip", "stops on no trip: b/s1"),
        ("van", "outside vans.count"),
        ("duplicate candidates", "duplicate candidate ids"),
        ("duplicate slots", "duplicate slot ids"),
        ("duplicate stops", "duplicate stop ids"),
        ("51 candidates", "at most 50"),
        ("13 slots", "at most 12"),
        ("no candidates", "at least 1"),
        ("near_m", "less than or equal to 5000"),
    ],
)
async def test_the_endpoint_refuses_an_inconsistent_request(change: str, message: str) -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        answer = await c.post(
            "/slots/price", json=broken(change), headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert answer.status_code == 422 and message in answer.text, answer.text


@pytest.mark.anyio
async def test_the_endpoint_needs_the_token_and_answers_route_costs() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        no = await c.post("/slots/price", json=valid())
        ok = await c.post(
            "/slots/price", json=valid(), headers={"Authorization": f"Bearer {TOKEN}"}
        )
    assert no.status_code == 401 and no.json()["code"] == "ml.unauthorized"
    assert ok.status_code == 200, ok.text
    answer = ok.json()
    assert answer["model"] == "slot-insertion-v0" and answer["source"] == "haversine"
    assert answer["base"]["trips"] == 1 and answer["base"]["problems"] == []
    assert [s["id"] for s in answer["candidates"][0]["slots"]] == ["s1", "s2"]


@pytest.mark.anyio
@pytest.mark.parametrize(("candidates", "budget_s"), [(1, 0.5), (50, 3.0)])
async def test_a_hundred_stops_in_six_slots_are_priced_fast(
    candidates: int, budget_s: float
) -> None:
    req = SlotPriceRequest.model_validate(kharghar(100, candidates))
    started = time.perf_counter()
    answer = await slots.price(req, None)
    assert time.perf_counter() - started < budget_s
    assert len(answer.candidates) == candidates
