"""`intel_gateway.api.price_slots` and `society_stop_costs` (M10 §3.8): the ML service within
`ml_slot_timeout_s`, else the rule fallback for a customer's quote; the ML service alone for M9's
stop cost, which a rule estimate never moves. The ML app itself answers one test, so the request
the gateway builds is held to the real `/slots/price`."""

import json
import uuid
from datetime import date
from typing import Any

import httpx2
import pytest
from gharbasket_ml.schemas import SlotPriceRequest as MlSlotPriceRequest
from gharbasket_ml.schemas import SlotPriceResponse as MlSlotPriceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api, fallback
from gharbasket.modules.intel_gateway.schemas import SlotCandidate, SlotDay, SlotPrices

from ..conftest import make_settings
from .test_plan_week import TOKEN, ml_transport

pytestmark = pytest.mark.anyio

LAT, LNG = 19.0300, 73.0690  # the hub
HUB = uuid.UUID("0199a000-0000-7000-8000-0000000000a1")
A, B, X, Y = (uuid.UUID(f"0199a000-0000-7000-8000-0000000000b{n}") for n in range(1, 5))
DAY = date(2026, 10, 27)
ML = make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN)


def h(hour: int) -> int:
    return hour * 3600


def stop(society: uuid.UUID, slot: str, dlat: float, start: int) -> dict[str, Any]:
    return {
        "id": f"{society}/{slot}",
        "lat": LAT + dlat,
        "lng": LNG,
        "orders": 3,
        "kg_tenths": 240,
        "crates": 1,
        "window_start_s": h(start),
        "window_end_s": h(start + 2),
    }


def three_stops(**changes: Any) -> SlotDay:
    """A at +0.007° in s1 (8-10), B at +0.011° in s2 (10-12), X's own stop in s3 (12-14), on one
    trip of one van; s4 (14-16) has no stop. 0.001° of latitude here is 144.5 m by road."""
    stops = [stop(A, "s1", 0.007, 8), stop(B, "s2", 0.011, 10), stop(X, "s3", 0.003, 12)]
    return SlotDay.model_validate(
        {
            "hub_id": HUB,
            "day": DAY,
            "routes_source": "previous",
            "hub": {"lat": LAT, "lng": LNG},
            "depart_s": h(6) + 1800,
            "return_by_s": h(18),
            "stops": stops,
            "trips": [{"van": 0, "stops": [s["id"] for s in stops]}],
            "vans": {"count": 1},
            "slots": [
                {"id": f"s{n}", "window_start_s": h(start), "window_end_s": h(start + 2)}
                for n, start in ((1, 8), (2, 10), (3, 12), (4, 14))
            ],
        }
        | changes
    )


def x(own_stops: str = "join") -> list[SlotCandidate]:
    return [
        SlotCandidate.model_validate(
            {"id": X, "lat": LAT + 0.003, "lng": LNG, "own_stops": own_stops}
        )
    ]


def by_slot(prices: SlotPrices) -> dict[str, dict[str, Any]]:
    return {s.id: s.model_dump() for s in prices.candidates[0].slots}


# --- the rule fallback ---------------------------------------------------------------------------


def test_the_fallback_on_a_hand_computed_three_stop_day() -> None:
    prices = fallback.price_slots(three_stops(), x())
    got = {
        k: (v["reason"], v["marginal_m"], v["marginal_s"], v["marginal_cost_paise"], v["rank"])
        for k, v in by_slot(prices).items()
    }
    # s1: d = 578 m to A (≤ near_m 1000): detour 1156 m, 1156 / 6 x 1.5 = 289 s + 240 drop + 30
    #     a crate = 559 s; 1156 m x 60 / 1000 = 69 + 559 x 3 = 1746.
    # s2: d = 1156 m to B: detour 2312 m, 578 + 270 = 848 s; 139 + 2544 = 2683.
    # s3: X's own stop: 30 s of crate, 90 paise.
    # s4: no stop, d = 434 m to the hub: detour 868 m, 217 + 270 = 487 s; 52 + 1461 = 1513.
    assert got == {
        "s1": ("NEIGHBOUR_STOP", 1156, 559, 1746, 3),
        "s2": ("NEW_STOP", 2312, 848, 2683, 4),
        "s3": ("SAME_SOCIETY", 0, 30, 90, 1),
        "s4": ("NEW_TRIP", 868, 487, 1513, 2),
    }
    assert [by_slot(prices)[s]["nearest_stop_m"] for s in ("s1", "s2", "s3", "s4")] == [
        578,
        1156,
        0,
        None,
    ]
    assert (prices.source, prices.model, prices.base) == ("fallback", "slot-nearest-v0", None)
    assert all(p["feasible"] and p["position"] is None for p in by_slot(prices).values())


def test_the_fallback_ignores_the_candidates_own_stops_when_asked() -> None:
    s3 = by_slot(fallback.price_slots(three_stops(), x("ignore")))["s3"]
    assert (s3["reason"], s3["marginal_cost_paise"]) == ("NEW_TRIP", 1513)


# --- price_slots ----------------------------------------------------------------------------------


def test_the_request_is_one_the_ml_service_accepts() -> None:
    sent = api._slot_body(three_stops(), x())  # pyright: ignore[reportPrivateUsage]
    assert sent is not None and "hub_id" not in sent and "routes_source" not in sent
    # The ML schema forbids extra fields; it adds only its defaults (near_m, max_late_s).
    taken = MlSlotPriceRequest.model_validate(sent).model_dump(mode="json")
    assert {k: taken[k] for k in sent} == sent and set(taken) - set(sent) == {
        "near_m",
        "max_late_s",
    }


async def test_the_ml_service_answers_with_its_own_numbers() -> None:
    async with httpx2.AsyncClient(transport=ml_transport()) as http:
        prices = await api.price_slots(http, ML, three_stops(), x())
    assert (prices.source, prices.distance_source, prices.model) == (
        "ml",
        "haversine",
        "slot-insertion-v0",
    )
    assert prices.base is not None and prices.base.trips == 1
    assert by_slot(prices)["s3"]["reason"] == "SAME_SOCIETY"
    assert prices.candidates[0].id == X


@pytest.mark.parametrize("failure", ["timeout", "server_error", "malformed", "not_json_object"])
async def test_a_failing_ml_service_gives_the_fallback_within_the_slot_timeout(
    failure: str,
) -> None:
    timeouts: list[float] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/slots/price"
        timeouts.append(request.extensions["timeout"]["read"])
        if failure == "timeout":
            raise httpx2.ReadTimeout("slow", request=request)
        if failure == "server_error":
            return httpx2.Response(500, json={"title": "boom"})
        return httpx2.Response(200, json={"nope": 1} if failure == "malformed" else [1, 2])

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        prices = await api.price_slots(http, ML, three_stops(), x())
        capped = make_settings(ml_service_url="http://ml.test", ml_timeout_s=1.0)
        await api.price_slots(http, capped, three_stops(), x())
    assert prices == fallback.price_slots(three_stops(), x())
    assert timeouts == [2.0, 1.0]  # ML_SLOT_TIMEOUT_S, never more than ML_TIMEOUT_S


async def test_no_service_or_a_day_the_service_would_refuse_never_calls_it() -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        return httpx2.Response(500)

    day = three_stops()
    twice = day.model_copy(update={"stops": [*day.stops, day.stops[0]]})
    no_slots = day.model_copy(update={"slots": []})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        unset = await api.price_slots(http, make_settings(), day, x())
        for refused in (twice, no_slots):
            assert (await api.price_slots(http, ML, refused, x())).source == "fallback"
    assert unset.source == "fallback" and calls == []


@pytest.mark.services
async def test_the_answer_is_kept_as_a_slot_price_prediction(db_session: AsyncSession) -> None:
    household = uuid.uuid4()
    async with httpx2.AsyncClient() as http:
        await api.price_slots(
            http,
            make_settings(),
            three_stops(),
            x(),
            session=db_session,
            subject_type="household",
            subject_id=household,
        )
    kept = await api.latest_prediction(
        db_session, kind="slot_price", subject_id=household, for_date=DAY
    )
    assert kept is not None and (kept.source, kept.model_name) == ("fallback", "slot-nearest-v0")
    assert kept.payload["candidates"][0]["id"] == str(X)


# --- society_stop_costs ---------------------------------------------------------------------------


def ml_answer(
    body: dict[str, Any], costs: dict[str, list[tuple[bool, int | None]]]
) -> dict[str, Any]:
    """An ML-shaped answer: per candidate id, (feasible, cost) for each slot of the request."""
    answer = {
        "source": "haversine",
        "base": {"trips": 1, "vans": 1, "km": 3.0, "cost_paise": 30_000},
        "candidates": [
            {
                "id": c["id"],
                "slots": [
                    {
                        "id": slot["id"],
                        "rank": n + 1,
                        "feasible": feasible,
                        "marginal_cost_paise": cost,
                        "marginal_m": None,
                        "marginal_s": None,
                        "nearest_stop_m": None,
                        "reason": "NEW_STOP" if feasible else "INFEASIBLE",
                        "position": None,
                    }
                    for n, (slot, (feasible, cost)) in enumerate(
                        zip(body["slots"], costs[c["id"]], strict=False)
                    )
                ],
            }
            for c in body["candidates"]
        ],
        "seconds": 0.01,
        "model": "slot-insertion-v0",
    }
    MlSlotPriceResponse.model_validate(answer)
    return answer


def two_days() -> list[SlotDay]:
    return [three_stops(), three_stops(day=date(2026, 10, 20))]


def xy() -> list[SlotCandidate]:
    return [SlotCandidate(id=c, lat=LAT + 0.003, lng=LNG, own_stops="ignore") for c in (X, Y)]


@pytest.mark.services
async def test_the_stop_cost_is_the_least_feasible_over_days_and_slots(
    db_session: AsyncSession,
) -> None:
    per_day = {
        # X: 900 on the first day; 700 on the second, where a cheaper slot is infeasible.
        # Y: never feasible.
        "2026-10-27": {str(X): [(True, 900), (True, 1200)], str(Y): [(False, None)] * 4},
        "2026-10-20": {str(X): [(True, 700), (False, 10)], str(Y): [(False, None)] * 4},
    }
    seen: list[tuple[float, int]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append((request.extensions["timeout"]["read"], len(body["candidates"])))
        costs = per_day["2026-10-27" if len(seen) == 1 else "2026-10-20"]
        return httpx2.Response(200, json=ml_answer(body, costs))

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        costs = await api.society_stop_costs(
            http, ML, two_days(), xy(), session=db_session, for_date=DAY
        )

    assert costs == {X: 700, Y: None}
    assert seen == [(3.0, 2), (3.0, 2)]  # ML_TIMEOUT_S a day, one call a day
    kept = await api.latest_prediction(db_session, kind="slot_price", subject_id=X, for_date=DAY)
    assert kept is not None and (kept.source, kept.model_name) == ("ml", "slot-insertion-v0")
    assert kept.payload == {"cost_paise": 700, "by_day": {"2026-10-27": 900, "2026-10-20": 700}}


@pytest.mark.parametrize("failure", ["timeout", "server_error", "unset"])
async def test_the_stop_cost_never_falls_back_to_the_rule(failure: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "timeout":
            raise httpx2.ReadTimeout("slow", request=request)
        return httpx2.Response(503)

    settings = make_settings() if failure == "unset" else ML
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        costs = await api.society_stop_costs(http, settings, two_days(), xy())
    assert costs == {X: None, Y: None}


async def test_more_than_fifty_societies_go_fifty_to_a_call() -> None:
    sizes: list[int] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        sizes.append(len(body["candidates"]))
        return httpx2.Response(
            200, json=ml_answer(body, {c["id"]: [(True, 500)] for c in body["candidates"]})
        )

    many = [SlotCandidate(id=uuid.uuid4(), lat=LAT, lng=LNG, own_stops="ignore") for _ in range(51)]
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        costs = await api.society_stop_costs(http, ML, [three_stops()], many)
    assert sizes == [50, 1] and set(costs.values()) == {500}
