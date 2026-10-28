"""Green slots (M3 §3.6, M10 §3.8): the saving rule, the 15-minute quote, the kill switch, and a
move at a quoted saving that stays on the box."""

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, time, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import service as orders_service
from gharbasket.modules.subscriptions.green import MIN_SAVING_PAISE, saving_paise
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..logistics.conftest import vehicle
from .test_cycles import MEENA, NOW, PRIYA, as_user, subscribe
from .test_edits import generate, key

pytestmark = [pytest.mark.anyio]

ML = {"ml_service_url": "http://ml.test", "ml_service_token": "green-test-token"}
CUTOFF = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)  # Mon 21:00 IST for Tuesday's box


# --- the saving rule ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("current", "slot", "share", "cap", "total", "saving"),
    [
        (4000, 600, 50, 1500, 90_000, 1500),  # half of ₹34 is ₹17: the ₹15 cap
        (4000, 1000, 50, 5000, 90_000, 1500),  # half of ₹30
        (4000, 1010, 50, 5000, 90_000, 1400),  # ₹14.95 rounds down to whole rupees
        (1000, 100, 50, 1500, 90_000, 0),  # ₹4.50 is under ₹5: no "save ₹4"
        (2000, 0, 50, 1500, 500, 500),  # at most the lines' total
        (4000, 600, 50, 1500, 499, 0),  # ... which under ₹5 is nothing
        (600, 4000, 50, 1500, 90_000, 0),  # costlier: never a surcharge
        (None, 600, 50, 1500, 90_000, 0),  # the current slot unpriced
        (4000, None, 50, 1500, 90_000, 0),  # an infeasible slot
        (4000, 600, 0, 1500, 90_000, 0),  # the hub passes nothing on
        (4000, 600, 100, 0, 90_000, 0),  # ... or caps it at 0
    ],
)
def test_the_saving_rule(
    current: int | None, slot: int | None, share: int, cap: int, total: int, saving: int
) -> None:
    got = saving_paise(current, slot, share_pct=share, max_paise=cap, total_paise=total)
    assert got == saving


@given(
    current=st.one_of(st.none(), st.integers(0, 10**7)),
    slot=st.one_of(st.none(), st.integers(0, 10**7)),
    share=st.integers(0, 100),
    cap=st.integers(0, 10_000),
    total=st.integers(0, 10**6),
    fee=st.integers(0, 10_000),
)
def test_a_saving_is_whole_rupees_within_its_bounds_and_never_a_negative_price(
    current: int | None, slot: int | None, share: int, cap: int, total: int, fee: int
) -> None:
    got = saving_paise(current, slot, share_pct=share, max_paise=cap, total_paise=total)
    assert 0 <= got <= min(cap, total) and got % 100 == 0
    assert got == 0 or got >= MIN_SAVING_PAISE
    if current is not None and slot is not None and slot >= current:
        assert got == 0
    assert total + fee - got >= 0  # payable_paise


# --- the endpoints ------------------------------------------------------------------------------


def fake_ml(costs: Mapping[uuid.UUID, tuple[int | None, str, int | None]], calls: list) -> Any:
    """A /slots/price that answers `costs` per slot (cost, reason, nearest metres) and counts."""

    def handle(request: httpx2.Request) -> httpx2.Response:
        if request.url.path != "/slots/price":
            return httpx2.Response(500)
        body = json.loads(request.content)
        calls.append(body)
        by_cost = sorted(
            body["slots"],
            key=lambda s: (costs[uuid.UUID(s["id"])][0] is None, costs[uuid.UUID(s["id"])][0] or 0),
        )
        rank = {s["id"]: n for n, s in enumerate(by_cost, start=1)}
        slots = []
        for s in body["slots"]:
            cost, reason, near = costs[uuid.UUID(s["id"])]
            feasible = reason != "INFEASIBLE"
            slots.append(
                {
                    "id": s["id"],
                    "rank": rank[s["id"]],
                    "feasible": feasible,
                    "marginal_cost_paise": cost,
                    "marginal_m": 0 if feasible else None,
                    "marginal_s": 0 if feasible else None,
                    "nearest_stop_m": near,
                    "reason": reason,
                    "position": None,
                }
            )
        return httpx2.Response(
            200,
            json={
                "source": "haversine",
                "base": {"trips": 1, "vans": 1, "km": 4.2, "cost_paise": 9000, "problems": []},
                "candidates": [{"id": c["id"], "slots": slots} for c in body["candidates"]],
                "seconds": 0.01,
                "model": "slot-insertion-v0",
            },
        )

    return httpx2.MockTransport(handle)


async def week(clients: ClientFactory, google: FakeGoogle, db: AsyncSession, *phones: str):
    """Subscribed households with Tuesday's box shown (seed slot 07-09), a 09-11 box slot and the
    seed hub."""
    api = clients()
    auths = []
    for phone in phones:
        auth, _ = await as_user(api, google, phone)
        await subscribe(api, auth)
        auths.append(auth)
    await generate(api)
    early = (await api.get("/v1/cycles/current", headers=auths[0])).json()["slot"]["id"]
    late = await orders_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label="Late 9-11",
        kind="box",
        start=time(9),
        end=time(11),
        capacity=50,
    )
    await seed_hubs(db)  # the hub's point and settings; the catalog's world has none
    await db.commit()
    return auths, uuid.UUID(early), late.id


@pytest.mark.services
@pytest.mark.usefixtures("delivery")
@time_machine.travel(NOW, tick=False)
async def test_off_both_endpoints_are_404_and_without_routes_the_quote_is_unpriced(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    (priya,), early, late = await week(clients, google, db_session, PRIYA)
    calls: list = []
    costs = {early: (4000, "NEW_STOP", 2300), late: (600, "NEIGHBOUR_STOP", 420)}
    http = httpx2.AsyncClient(transport=fake_ml(costs, calls))
    off = clients(http=http, **ML)
    cycle = (await off.get("/v1/cycles/current", headers=priya)).json()

    quoted = await off.post(f"/v1/cycles/{cycle['id']}/slot-quotes", headers=priya)
    moved = await off.put(
        f"/v1/cycles/{cycle['id']}/slot",
        json={"slot_id": str(late), "quote_id": str(uuid.uuid4())},
        headers={**priya, "If-Match": '"1"'},
    )
    assert (quoted.status_code, quoted.json()["code"]) == (404, "subscriptions.green_slots_off")
    assert (moved.status_code, moved.json()["code"]) == (404, "subscriptions.green_slots_off")

    # On, but the hub has no box vehicle: no tentative day, so no ML call and no money.
    on = clients(http=http, green_slots_enabled=True, **ML)
    unpriced = await on.post(f"/v1/cycles/{cycle['id']}/slot-quotes", headers=priya)
    assert unpriced.status_code == 201, unpriced.text
    body = unpriced.json()
    assert body["source"] == "unpriced" and calls == []
    assert [i["slot"]["id"] for i in body["items"]] == [str(early), str(late)]
    assert {
        (i["reason_code"], i["green"], i["saving_paise"], i["rank"]) for i in body["items"]
    } == {("unpriced", False, 0, None)}
    assert [i["current"] for i in body["items"]] == [True, False]
    await http.aclose()


@pytest.mark.services
@pytest.mark.usefixtures("delivery")
@time_machine.travel(NOW, tick=False)
async def test_a_quote_lives_15_minutes_and_says_where_its_numbers_came_from(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    (priya, _), early, late = await week(clients, google, db_session, PRIYA, MEENA)
    await vehicle(db_session, "EV-1")
    calls: list = []
    costs = {early: (4000, "NEW_STOP", 2340), late: (600, "NEIGHBOUR_STOP", 420)}
    http = httpx2.AsyncClient(transport=fake_ml(costs, calls))
    api = clients(http=http, green_slots_enabled=True, **ML)
    cycle_id = (await api.get("/v1/cycles/current", headers=priya)).json()["id"]
    url = f"/v1/cycles/{cycle_id}/slot-quotes"

    first = await api.post(url, headers=priya)
    again = await api.post(url, headers=priya)

    assert (first.status_code, again.status_code) == (201, 200)
    quote = first.json()
    assert again.json() == quote and len(calls) == 1
    assert quote["source"] == "ml" and quote["expires_at"] == "2026-10-10T10:15:00Z"
    current, moved = quote["items"]
    assert current == {
        "slot": current["slot"] | {"id": str(early)},
        "current": True,
        "available": current["available"],
        "green": False,
        "saving_paise": 0,
        "rank": 2,
        "reason_code": "new_stop",
        "reason_params": {"metres": 2300},
    }
    assert (moved["slot"]["id"], moved["green"], moved["saving_paise"], moved["rank"]) == (
        str(late),
        True,
        1500,  # half of ₹34, capped at ₹15
        1,
    )
    assert (moved["reason_code"], moved["reason_params"], moved["available"]) == (
        "neighbour_stop",
        {"metres": 400},
        50,
    )
    assert "marginal_cost_paise" not in json.dumps(quote)
    # The household's own box is the one priced: only Meena's is expected on the routes.
    (body,) = calls
    assert [s["orders"] for s in body["stops"]] == [1]
    assert [c["lat"] for c in body["candidates"]] and body["candidates"][0]["orders"] == 1

    with time_machine.travel(NOW + timedelta(minutes=16), tick=False):
        priya, _ = await as_user(api, google, PRIYA)  # access tokens live 15 minutes too
        renewed = await api.post(url, headers=priya)
    assert renewed.status_code == 201 and renewed.json()["id"] != quote["id"]
    assert len(calls) == 2

    down = clients(
        http=httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(500))),
        green_slots_enabled=True,
        **ML,
    )
    with time_machine.travel(NOW + timedelta(minutes=32), tick=False):
        priya, _ = await as_user(down, google, PRIYA)  # the first token has expired
        fallback = await down.post(url, headers=priya)
    assert fallback.status_code == 201 and fallback.json()["source"] == "fallback"
    for item in fallback.json()["items"]:
        assert item["rank"] is not None and item["saving_paise"] % 100 == 0
    await http.aclose()


@pytest.mark.services
@pytest.mark.usefixtures("delivery")
@time_machine.travel(NOW, tick=False)
async def test_a_move_at_a_quoted_saving_is_kept_on_the_box(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    (priya, meena), early, late = await week(clients, google, db_session, PRIYA, MEENA)
    await vehicle(db_session, "EV-1")
    calls: list = []
    costs = {early: (4000, "NEW_STOP", 2340), late: (600, "NEIGHBOUR_STOP", 420)}
    http = httpx2.AsyncClient(transport=fake_ml(costs, calls))
    api = clients(http=http, green_slots_enabled=True, **ML)
    box = (await api.get("/v1/cycles/current", headers=priya)).json()
    theirs = (await api.get("/v1/cycles/current", headers=meena)).json()
    assert (box["fees_paise"], box["discount_paise"], box["green_slot"]) == (0, 0, None)
    assert box["payable_paise"] == box["total_paise"]
    slot_url = f"/v1/cycles/{box['id']}/slot"
    quote = (await api.post(f"/v1/cycles/{box['id']}/slot-quotes", headers=priya)).json()
    foreign = (await api.post(f"/v1/cycles/{theirs['id']}/slot-quotes", headers=meena)).json()

    async def put(slot: uuid.UUID | str, quote_id: str, version: int) -> httpx2.Response:
        return await api.put(
            slot_url,
            json={"slot_id": str(slot), "quote_id": quote_id},
            headers={**priya, "If-Match": f'"{version}"'},
        )

    refusals = [
        await put(late, str(uuid.uuid4()), 1),
        await put(late, foreign["id"], 1),
        await put(uuid.uuid4(), quote["id"], 1),
        await put(late, quote["id"], 9),
    ]
    assert [(r.status_code, r.json()["code"]) for r in refusals] == [
        (422, "subscriptions.quote_invalid"),
        (422, "subscriptions.quote_invalid"),
        (422, "subscriptions.quote_invalid"),
        (412, "subscriptions.version_conflict"),
    ]

    moved = await put(late, quote["id"], 1)
    assert moved.status_code == 200, moved.text
    after = moved.json()
    assert (after["version"], after["slot"]["id"], moved.headers["ETag"]) == (2, str(late), '"2"')
    assert after["green_slot"] == {
        "quote_id": quote["id"],
        "saving_paise": 1500,
        "reason_code": "neighbour_stop",
    }
    assert after["discount_paise"] == 1500
    assert after["payable_paise"] == after["total_paise"] - 1500
    assert after["delivery_date"] == box["delivery_date"]
    assert after["cutoff_at"] == box["cutoff_at"]
    # The quote was made for the old slot: it no longer applies.
    stale = await put(early, quote["id"], 2)
    assert (stale.status_code, stale.json()["code"]) == (409, "subscriptions.quote_expired")
    fresh = (await api.post(f"/v1/cycles/{box['id']}/slot-quotes", headers=priya)).json()
    assert fresh["id"] != quote["id"]
    assert [i["current"] for i in fresh["items"]] == [False, True]
    assert [i["saving_paise"] for i in fresh["items"]] == [0, 0]  # back costs more: no fee either
    with time_machine.travel(NOW + timedelta(minutes=15), tick=False):
        expired = await put(early, fresh["id"], 2)  # the token, 15 minutes old, still holds
    assert (expired.status_code, expired.json()["code"]) == (409, "subscriptions.quote_expired")

    approved = await api.post(
        f"/v1/cycles/{box['id']}/approve", headers={**priya, **key(), "If-Match": '"2"'}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["discount_paise"] == 1500  # kept through approval
    order = (await api.get(f"/v1/orders/{approved.json()['order_id']}", headers=priya)).json()
    assert order["slot"]["id"] == str(late)

    with time_machine.travel(CUTOFF, tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        late_quote = await api.post(f"/v1/cycles/{box['id']}/slot-quotes", headers=priya)
    assert (late_quote.status_code, late_quote.json()["code"]) == (
        409,
        "subscriptions.cutoff_passed",
    )
    await http.aclose()
