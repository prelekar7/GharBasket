"""`GET /v1/analytics/unit-economics`, `/pnl` and the hub's assumptions (M12 §3.4) on a small seeded
fact set whose expected paise are worked out by hand in the comments: measured against assumed
inputs, what-if overrides, the P&L's lines, data sources never pooled, empty periods, the role
scoping, and the assumptions PUT (If-Match, validation, history, audit)."""

import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import economics, refresh, router
from gharbasket.modules.analytics.models import (
    DEFAULT_ASSUMPTIONS,
    Assumption,
    AssumptionChange,
    FactHubDay,
    RefreshRun,
)
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import SEED_HUB_ID

from .. import seed_cache as cached
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import as_user
from .test_kpis import (
    NEHA,
    NOW,
    OTHER_HUB,
    PRIYA,
    SANJAY,
    TUESDAY1,
    TUESDAY2,
    VIKRAM,
    WEEK1,
    WEEK2,
    earning,
    order,
    route,
    signed_in,
    walk_keys,
)

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.services,
    pytest.mark.usefixtures("delivery", "hubs"),
]

HUB = SEED_HUB_ID
DEFAULTS = {
    "aov_paise": 62_000,
    "margin_bp": 2_400,
    "delivery_paise": 4_000,
    "packing_paise": 3_000,
    "captain_paise": 600,
    "payment_fee_bp": 120,
    "cac_paise": 1_300,
}
# The two seeded weeks as the unit-economics endpoint reads them (Monday 26 Oct to Sunday 8 Nov)
RANGE = {"from": "2026-10-26", "to": "2026-11-08"}


@pytest.fixture
async def hubs(db_session: AsyncSession) -> None:
    await cached.seed_hubs(db_session)


@pytest.fixture(autouse=True)
def clock() -> Iterator[None]:
    with time_machine.travel(NOW, tick=False):
        yield


async def seed_economics(session: AsyncSession, *, ran: bool = True) -> None:
    """The seed hub's two weeks, and beside them what must never reach its real numbers.

    Week of 26 Oct (Tuesday 27 Oct), real:
    - delivered boxes 60,000 · 64,000 (of which a 4,000 pre-live fee, 2,000 credited) · 62,000,
      so box revenue net of fee and credit is 60,000 + 58,000 + 62,000 = 180,000, fees 4,000;
    - a delivered add-on 5,000 (food revenue 185,000); two delivered tiffins 9,000 + 11,000;
    - a box route costing 30,000 for 3 orders, a tiffin route (1,000) that is not a box route;
    - earnings: captain 1,800, rider 5,000, kitchen 12,000.
    Week of 2 Nov (Tuesday 3 Nov), real: one box 64,000, a box route 12,000 for 1 order, a packer's
    earnings of 400 (no captain earnings yet).
    """
    session.add_all(
        [
            order(TUESDAY1, total_paise=60_000),
            order(TUESDAY1, total_paise=64_000, fees_paise=4_000, credited_paise=2_000),
            order(TUESDAY1, total_paise=62_000),
            order(TUESDAY1, kind="addon", total_paise=5_000),
            order(TUESDAY1, kind="tiffin", total_paise=9_000),
            order(TUESDAY1, kind="tiffin", total_paise=11_000),
            order(TUESDAY1, status="undelivered", total_paise=50_000, on_time=None),
            order(TUESDAY2, total_paise=64_000),
            # Not this hub's real numbers: another hub, and the simulator's world
            order(TUESDAY1, hub_id=OTHER_HUB, total_paise=70_000),
            order(TUESDAY1, total_paise=99_999, data_source="synthetic"),
            route(TUESDAY1, cost_paise=30_000, orders=3),
            route(TUESDAY1, kind="tiffin", cost_paise=1_000, orders=2),
            route(TUESDAY2, cost_paise=12_000, orders=1),
            route(TUESDAY1, hub_id=OTHER_HUB, cost_paise=77_777, orders=1),
            earning(1, WEEK1, "captain", 1_800),
            earning(2, WEEK1, "rider", 5_000),
            earning(3, WEEK1, "kitchen", 12_000),
            earning(4, WEEK1, "captain", 9_999, hub_id=OTHER_HUB),
            earning(5, WEEK2, "packer", 400),
            earning(6, WEEK1, "captain", 8_888, data_source="synthetic"),
        ]
    )
    await session.flush()
    if ran:
        session.add(
            RefreshRun(
                job=refresh.NIGHTLY,
                scheduled_for=NOW - timedelta(hours=10),
                window_from=TUESDAY2 - timedelta(days=35),
                window_to=TUESDAY2,
                status="ok",
                started_at=NOW - timedelta(hours=10),
                finished_at=NOW - timedelta(hours=9),
            )
        )
        await session.flush()
    await refresh.refresh_views(session, refresh.VIEWS)


async def get_ue(api: httpx2.AsyncClient, auth: dict[str, str], **params: Any) -> httpx2.Response:
    query = {"hub_id": str(HUB), "data_source": "real", **RANGE, **params}
    return await api.get(
        "/v1/analytics/unit-economics",
        params={k: v for k, v in query.items() if v is not None},
        headers=auth,
    )


async def get_pnl(api: httpx2.AsyncClient, auth: dict[str, str], **params: Any) -> httpx2.Response:
    return await api.get(
        "/v1/analytics/pnl",
        params={"hub_id": str(HUB), "data_source": "real", "period": "2026-10", **params},
        headers=auth,
    )


def assumptions_url(hub: uuid.UUID = HUB) -> str:
    return f"/v1/analytics/hubs/{hub}/assumptions"


def mapped(lines: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {line["key"]: line for line in lines}


# --- unit economics ----------------------------------------------------------------------------


async def test_measured_inputs_fill_the_per_box_economics(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await get_ue(api, owner)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert answer.headers["Cache-Control"] == "private, no-cache"
    assert (body["hub_id"], body["data_source"]) == (str(HUB), "real")
    assert (body["from"], body["to"], body["boxes"]) == ("2026-10-26", "2026-11-08", 4)
    assert body["as_of"] is not None
    assert body["assumptions"] == DEFAULTS
    assert body["measured"] == {
        "aov_paise": 62_250,  # (180,000 + 64,000 boxes + 5,000 add-on) ÷ 4
        "margin_bp": None,  # no COGS yet (M6 §3.4)
        "delivery_paise": 10_500,  # (30,000 + 12,000 box route cost) ÷ (3 + 1 orders)
        # Each over the weeks that measured it, so the week without packer (or captain) earnings
        # does not water the rate down with its boxes:
        "packing_paise": 400,  # 400 of packer earnings ÷ week 2's 1 box (not ÷ 4)
        "captain_paise": 600,  # 1,800 of captain earnings ÷ week 1's 3 boxes (not ÷ 4)
    }
    assert body["used"] == {
        **DEFAULTS,
        "aov_paise": 62_250,
        "delivery_paise": 10_500,
        "packing_paise": 400,
        "captain_paise": 600,
    }
    assert body["sources"] == {
        "aov_paise": "measured",
        "margin_bp": "assumed",
        "delivery_paise": "measured",
        "packing_paise": "measured",
        "captain_paise": "measured",
        "payment_fee_bp": "assumed",
        "cac_paise": "assumed",
        "delivery_fee_paise": "measured",
    }
    assert body["delivery_fee_paise"] == 1_000  # 4,000 of fees ÷ 4 boxes
    assert body["per_box"] == {
        "margin_paise": 14_940,  # 62,250 x 24 %
        "delivery_fee_paise": 1_000,
        "delivery_paise": 10_500,
        "packing_paise": 400,
        "captain_paise": 600,
        "fee_paise": 747,  # 62,250 x 1.2 %
        "cac_paise": 1_300,
        # 14,940 + 1,000 - 10,500 - 400 - 600 - 747 - 1,300
        "contribution_paise": 2_393,
    }
    assert body["weeks"] == [
        # Week 1 on its own: AOV 185,000 ÷ 3 = 61,667, fee 4,000 ÷ 3 = 1,333, delivery 30,000 ÷ 3,
        # captain 1,800 ÷ 3, no packer earnings (3,000 assumed): 14,800 + 1,333 - 10,000 - 3,000
        # - 600 - 740 - 1,300
        {"week": "2026-10-26", "boxes": 3, "contribution_paise": 493},
        # Week 2: AOV 64,000, no fee, delivery 12,000, packing 400, captain 600 assumed:
        # 15,360 - 12,000 - 400 - 600 - 768 - 1,300
        {"week": "2026-11-02", "boxes": 1, "contribution_paise": 292},
    ]
    assert not {"household_id", "phone", "flat", "address", "name"} & set(walk_keys(body))


async def test_what_if_overrides_beat_measured_and_are_never_saved(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await get_ue(api, owner, aov_paise=70_000, margin_bp=3_000, delivery_fee_paise=0)

    body = answer.json()
    assert answer.status_code == 200, answer.text
    assert body["used"]["aov_paise"] == 70_000 and body["used"]["margin_bp"] == 3_000
    assert body["used"]["delivery_paise"] == 10_500  # still measured
    assert body["sources"]["aov_paise"] == "override"
    assert body["sources"]["margin_bp"] == "override"
    assert body["sources"]["delivery_paise"] == "measured"
    assert body["sources"]["delivery_fee_paise"] == "override"
    assert body["measured"]["aov_paise"] == 62_250  # the measurement itself is unchanged
    assert body["delivery_fee_paise"] == 0
    # 21,000 margin + 0 - 10,500 - 400 - 600 - 840 (70,000 x 1.2 %) - 1,300
    assert body["per_box"]["margin_paise"] == 21_000
    assert body["per_box"]["contribution_paise"] == 7_360
    # Nothing was saved
    saved = await api.get(assumptions_url(), headers=owner)
    assert saved.json()["assumptions"] == DEFAULTS and saved.json()["version"] == 1


async def test_prefer_assumed_ignores_measurements(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    body = (await get_ue(api, owner, prefer="assumed")).json()

    assert body["used"] == DEFAULTS
    assert set(body["sources"].values()) == {"assumed", "measured"}
    assert {k: v for k, v in body["sources"].items() if k != "delivery_fee_paise"} == dict.fromkeys(
        DEFAULTS, "assumed"
    )
    # The delivery fee is revenue that was measured, not an assumption:
    # 14,880 + 1,000 - 4,000 - 3,000 - 600 - 744 - 1,300
    assert body["per_box"]["contribution_paise"] == 6_236
    assert body["measured"]["aov_paise"] == 62_250  # still shown


async def test_per_box_arithmetic_matches_the_consoles_box_economics(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """The cases of `frontend/console/lib/unit-economics.test.js`, on a hub with nothing measured:
    percentage steps round half up to a whole paisa."""
    api, owner = await signed_in(clients, google, SANJAY)
    cases: list[tuple[dict[str, int], int, int, int | None]] = [
        # overrides, fee revenue, expected fee_paise, expected contribution
        ({}, 0, 744, 5_236),
        ({"delivery_fee_paise": 410}, 410, 744, 5_646),
        ({"aov_paise": 70_000, "margin_bp": 2_000, "cac_paise": 0}, 0, 840, 5_560),
        ({"margin_bp": 0}, 0, 744, -(4_000 + 3_000 + 600 + 744 + 1_300)),
        # 10,120 x 1.25 % = 126.5 paise: half up is 127 (half to even would be 126)
        ({"aov_paise": 10_120, "payment_fee_bp": 125, "margin_bp": 0}, 0, 127, None),
    ]
    for overrides, fee, expected_fee, contribution in cases:
        body = (await get_ue(api, owner, **overrides)).json()
        assert body["boxes"] == 0 and body["delivery_fee_paise"] == fee
        assert body["per_box"]["fee_paise"] == expected_fee, overrides
        if contribution is not None:
            assert body["per_box"]["contribution_paise"] == contribution, overrides


async def test_measured_margin_once_the_cogs_is_recorded(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    for day, cogs in ((TUESDAY1, 100_000), (TUESDAY2, 40_000)):
        db_session.add(
            FactHubDay(
                hub_id=HUB,
                on_date=day,
                packed_g=50_000,
                waste_ngo_g=0,
                waste_bin_g=0,
                waste_kitchen_g=0,
                waste_imperfect_g=0,
                cogs_paise=cogs,
                surplus_offered_g=0,
                surplus_taken_g=0,
                data_source="real",
            )
        )
    await db_session.flush()
    await refresh.refresh_views(db_session, refresh.VIEWS)
    api, owner = await signed_in(clients, google, SANJAY)

    body = (await get_ue(api, owner)).json()

    # 1 - (140,000 ÷ 249,000) = 43.78 % of food revenue is margin: 4,378 bp
    assert body["measured"]["margin_bp"] == 4_378
    assert body["sources"]["margin_bp"] == "measured"
    pnl = mapped((await get_pnl(api, owner, period="2026-11")).json()["lines"])
    assert pnl["produce"]["amount_paise"] == -40_000 and pnl["produce"]["source"] == "measured"
    # 40,000 of COGS over 64,000 of food revenue: 62.5 % of it
    assert pnl["produce"]["basis"] == {"kind": "share", "bp": 6_250, "of": "box_revenue"}


async def test_data_sources_are_never_pooled(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    synthetic = (await get_ue(api, owner, data_source="synthetic")).json()
    real = (await get_ue(api, owner)).json()

    assert (synthetic["data_source"], synthetic["boxes"]) == ("synthetic", 1)
    assert synthetic["measured"]["aov_paise"] == 99_999
    assert synthetic["measured"]["captain_paise"] == 8_888  # the synthetic partner's week
    assert real["boxes"] == 4 and real["measured"]["aov_paise"] == 62_250
    pnl = (await get_pnl(api, owner, data_source="synthetic")).json()
    assert pnl["boxes"] == 1 and pnl["tiffins"] == 0 and pnl["revenue_paise"] == 99_999


async def test_no_data_source_is_422(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)

    for path, extra in (("unit-economics", {}), ("pnl", {"period": "2026-10"})):
        answer = await api.get(
            f"/v1/analytics/{path}", params={"hub_id": str(HUB), **extra}, headers=owner
        )
        assert answer.status_code == 422
        assert answer.json()["code"] == "analytics.data_source_required"
    wrong = await get_ue(api, owner, data_source="pilot")
    assert wrong.status_code == 422


async def test_an_empty_period_is_zero_with_assumed_inputs(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session, ran=False)
    api, owner = await signed_in(clients, google, SANJAY)

    body = (await get_ue(api, owner, **{"from": "2026-01-05", "to": "2026-01-18"})).json()

    assert body["boxes"] == 0 and body["as_of"] is None
    assert body["measured"] == dict.fromkeys(body["measured"])
    assert body["used"] == DEFAULTS and body["delivery_fee_paise"] == 0
    assert body["per_box"]["contribution_paise"] == 5_236  # the defaults' economics, 52.36
    assert body["weeks"] == [
        {"week": "2026-01-05", "boxes": 0, "contribution_paise": None},
        {"week": "2026-01-12", "boxes": 0, "contribution_paise": None},
    ]
    pnl = (await get_pnl(api, owner, period="2026-05")).json()
    assert (pnl["boxes"], pnl["tiffins"], pnl["revenue_paise"], pnl["contribution_paise"]) == (
        0,
        0,
        0,
        0,
    )
    assert pnl["per_box_paise"] is None and pnl["previous_contribution_paise"] is None
    assert {line["amount_paise"] for line in pnl["lines"]} == {0}
    # The payment fee and the CAC are never measured, even in a month with nothing in it
    empty = mapped(pnl["lines"])
    assert empty["fees"]["source"] == empty["acquisition"]["source"] == "assumed"
    assert [w["week"] for w in pnl["weeks"]] == [
        "2026-05-04",
        "2026-05-11",
        "2026-05-18",
        "2026-05-25",
    ]
    assert set(pnl["partners_paise"].values()) == {0}


async def test_the_range_limits(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)

    backwards = await get_ue(api, owner, **{"from": "2026-11-08", "to": "2026-11-01"})
    too_long = await get_ue(api, owner, **{"from": "2024-01-01", "to": "2026-11-08"})
    default = await get_ue(api, owner, **{"from": None, "to": None})

    assert backwards.json()["code"] == "analytics.range_invalid"
    assert too_long.json()["code"] == "analytics.range_too_long"
    assert default.status_code == 200
    assert len(default.json()["weeks"]) == 12  # the last 12 ISO weeks, this one included
    assert default.json()["to"] == "2026-11-08"


async def test_override_values_out_of_range_are_422_with_errors(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await get_ue(api, owner, aov_paise=5, payment_fee_bp=501, delivery_fee_paise=20_001)

    assert answer.status_code == 422
    body = answer.json()
    assert body["code"] == "analytics.assumptions_invalid"
    assert [e["field"] for e in body["errors"]] == [
        "aov_paise",
        "payment_fee_bp",
        "delivery_fee_paise",
    ]
    assert (await get_ue(api, owner, aov_paise=10_000, payment_fee_bp=500)).status_code == 200


# --- the P&L -----------------------------------------------------------------------------------


async def test_pnl_lines_on_a_known_month(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await get_pnl(api, owner)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert answer.headers["Cache-Control"] == "private, no-cache"
    assert (body["hub_id"], body["period"], body["boxes"], body["tiffins"]) == (
        str(HUB),
        "2026-10",
        3,
        2,
    )
    assert body["as_of"] is not None
    # Week of 26 Oct is the only one with facts; October has no month before it with any
    assert body["previous_contribution_paise"] is None
    assert body["partners_paise"] == {
        "captains": 1_800,
        "kitchens": 12_000,
        "riders": 5_000,
        "packers": 0,
    }
    assert [line["key"] for line in body["lines"]] == [
        "box_revenue",
        "delivery_fee_revenue",
        "tiffin_revenue",
        "produce",
        "kitchen",
        "delivery",
        "packing",
        "captain",
        "fees",
        "acquisition",
    ]
    lines = mapped(body["lines"])
    times = lambda qty, unit, rate: {  # noqa: E731
        "kind": "times",
        "qty": qty,
        "unit": unit,
        "rate_paise": rate,
    }
    expected: dict[str, tuple[int, str, dict[str, Any]]] = {
        # Food revenue net of fee and credit, add-on included: 180,000 + 5,000
        "box_revenue": (185_000, "measured", times(3, "box", 61_667)),
        "delivery_fee_revenue": (4_000, "measured", times(3, "box", 1_333)),
        "tiffin_revenue": (20_000, "measured", times(2, "tiffin", 10_000)),
        # No COGS: the 76 % that is not margin, of 185,000
        "produce": (-140_600, "assumed", {"kind": "share", "bp": 7_600, "of": "box_revenue"}),
        "kitchen": (-12_000, "measured", times(2, "tiffin", 6_000)),
        "delivery": (-30_000, "measured", times(3, "box", 10_000)),
        "packing": (-9_000, "assumed", times(3, "box", 3_000)),  # no packer earnings
        "captain": (-1_800, "measured", times(3, "box", 600)),
        "fees": (-2_220, "assumed", {"kind": "share", "bp": 120, "of": "box_revenue"}),
        "acquisition": (-3_900, "assumed", times(3, "box", 1_300)),
    }
    for key, (amount, source, basis) in expected.items():
        assert (lines[key]["amount_paise"], lines[key]["source"], lines[key]["basis"]) == (
            amount,
            source,
            basis,
        ), key
    assert body["revenue_paise"] == 209_000  # 185,000 + 4,000 + 20,000
    # 209,000 - 140,600 - 12,000 - 30,000 - 9,000 - 1,800 - 2,220 - 3,900
    assert body["contribution_paise"] == 9_480
    assert body["per_box_paise"] == 3_160
    assert body["contribution_paise"] == sum(line["amount_paise"] for line in body["lines"])
    assert body["weeks"] == [
        {"week": "2026-10-05", "contribution_paise": 0},
        {"week": "2026-10-12", "contribution_paise": 0},
        {"week": "2026-10-19", "contribution_paise": 0},
        {"week": "2026-10-26", "contribution_paise": 9_480},
    ]


async def test_pnl_of_the_next_month_and_its_previous_contribution(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    body = (await get_pnl(api, owner, period="2026-11")).json()

    assert (body["boxes"], body["tiffins"]) == (1, 0)
    assert body["previous_contribution_paise"] == 9_480  # October's
    lines = mapped(body["lines"])
    assert lines["packing"]["amount_paise"] == -400 and lines["packing"]["source"] == "measured"
    assert lines["captain"]["amount_paise"] == -600 and lines["captain"]["source"] == "assumed"
    assert lines["kitchen"]["amount_paise"] == 0
    assert lines["delivery_fee_revenue"]["amount_paise"] == 0
    # 64,000 - 48,640 (76 %) - 12,000 - 400 - 600 - 768 (1.2 %) - 1,300
    assert body["contribution_paise"] == body["per_box_paise"] == 292
    assert body["partners_paise"] == {"captains": 0, "kitchens": 0, "riders": 0, "packers": 400}
    assert [w["week"] for w in body["weeks"]] == [
        "2026-11-02",
        "2026-11-09",
        "2026-11-16",
        "2026-11-23",
        "2026-11-30",
    ]
    assert sum(w["contribution_paise"] for w in body["weeks"]) == body["contribution_paise"]


async def test_pnl_uses_the_hubs_saved_assumptions_for_unmeasured_lines(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    api, owner = await signed_in(clients, google, SANJAY)
    saved = await api.put(
        assumptions_url(),
        json={**DEFAULTS, "cac_paise": 2_000, "margin_bp": 3_000},
        headers={**owner, "If-Match": '"1"'},
    )
    assert saved.status_code == 200, saved.text

    lines = mapped((await get_pnl(api, owner)).json()["lines"])

    assert lines["acquisition"]["amount_paise"] == -6_000  # 3 boxes x 2,000
    assert lines["produce"]["amount_paise"] == -129_500  # 70 % of 185,000
    assert lines["produce"]["basis"] == {"kind": "share", "bp": 7_000, "of": "box_revenue"}


async def test_pnl_period_must_be_one_of_the_last_24_months(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)

    codes = {
        period: (await get_pnl(api, owner, period=period))
        for period in ("2026-11", "2024-12", "2024-11", "2026-12", "2026-13", "2026-1", "x")
    }

    assert codes["2026-11"].status_code == 200 and codes["2024-12"].status_code == 200
    assert codes["2024-11"].json()["code"] == "analytics.range_too_long"
    assert codes["2026-12"].json()["code"] == "analytics.range_invalid"  # not begun
    assert codes["2026-13"].json()["code"] == "analytics.range_invalid"
    assert codes["2026-1"].json()["code"] == "analytics.range_invalid"
    assert codes["x"].json()["code"] == "analytics.range_invalid"


async def test_a_week_without_boxes_does_not_make_the_box_lines_assumed() -> None:
    """A tiffin-only week has nothing for the box lines to measure: the month's captain and
    delivery lines stay measured, while its unpaid tiffins make the kitchen line assumed."""
    a = DEFAULT_ASSUMPTIONS
    boxes = economics.Week(
        boxes=2,
        box_revenue_paise=120_000,
        box_route_cost_paise=8_000,
        box_route_orders=2,
        captain_paise=1_200,
        packer_paise=800,
        cogs_paise=90_000,
    )
    tiffins = economics.Week(tiffins=3, tiffin_revenue_paise=30_000, cogs_paise=0)
    month = economics.merge(
        (w, economics.statement_of(w, a)) for w in (boxes, tiffins, economics.NOTHING)
    )
    sources = {key: line.measured for key, line in month.lines.items()}
    assert sources == {
        "box_revenue": True,
        "delivery_fee_revenue": True,
        "tiffin_revenue": True,
        "produce": True,
        "kitchen": False,  # 3 tiffins, no kitchen earnings: 0 and assumed
        "delivery": True,
        "packing": True,
        "captain": True,
        "fees": False,
        "acquisition": False,
    }
    assert month.lines["captain"].amount == -1_200 and month.lines["delivery"].amount == -8_000


async def test_the_range_measures_each_input_over_the_weeks_that_measured_it() -> None:
    measured = economics.measured_over(
        [
            # captain earnings and a costed route, no COGS
            economics.Week(
                boxes=3,
                box_revenue_paise=180_000,
                box_route_cost_paise=30_000,
                box_route_orders=3,
                captain_paise=1_800,
            ),
            # a route without a recorded cost and no captain earnings yet, but a COGS
            economics.Week(
                boxes=1,
                box_revenue_paise=60_000,
                box_route_cost_paise=None,
                box_route_orders=1,
                cogs_paise=45_000,
            ),
        ]
    )
    assert measured == {
        "aov_paise": 60_000,  # 240,000 ÷ 4: every week
        "margin_bp": 2_500,  # 1 - 45,000 ÷ 60,000: the week with a COGS
        "delivery_paise": 10_000,  # 30,000 ÷ 3: the costed week
        "packing_paise": None,
        "captain_paise": 600,  # 1,800 ÷ 3, not ÷ 4
    }


async def test_a_month_is_the_weeks_that_start_in_it() -> None:
    assert economics.mondays_in(date(2026, 11, 1), date(2026, 11, 30)) == [
        date(2026, 11, 2),
        date(2026, 11, 9),
        date(2026, 11, 16),
        date(2026, 11, 23),
        date(2026, 11, 30),
    ]
    assert economics.mondays_in(date(2027, 2, 1), date(2027, 2, 28))[0] == date(2027, 2, 1)
    assert len(economics.mondays_in(date(2027, 2, 1), date(2027, 2, 28))) == 4


# --- scope -------------------------------------------------------------------------------------


async def test_role_scoping_of_every_economics_endpoint(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_economics(db_session)
    owner_api, owner = await signed_in(clients, google, SANJAY)
    manager_api, manager = await signed_in(clients, google, VIKRAM)
    customer_api, customer = await signed_in(clients, google, PRIYA)
    admin_api, admin = await signed_in(clients, google, NEHA)
    other = OTHER_HUB
    body = {**DEFAULTS}

    async def statuses(
        api: httpx2.AsyncClient, auth: dict[str, str], hub: uuid.UUID | None
    ) -> list[int]:
        params: dict[str, Any] = {} if hub is None else {"hub_id": str(hub)}
        ue = await api.get(
            "/v1/analytics/unit-economics",
            params=params | RANGE | {"data_source": "real"},
            headers=auth,
        )
        pnl = await api.get(
            "/v1/analytics/pnl",
            params=params | {"data_source": "real", "period": "2026-10"},
            headers=auth,
        )
        reads = [ue.status_code, pnl.status_code]
        if hub is not None:
            reads.append((await api.get(assumptions_url(hub), headers=auth)).status_code)
            put = await api.put(
                assumptions_url(hub), json=body, headers={**auth, "If-Match": '"1"'}
            )
            reads.append(put.status_code)
        return reads

    # A franchise owner: their own hub only, and never without naming it
    assert await statuses(owner_api, owner, HUB) == [200, 200, 200, 200]
    assert await statuses(owner_api, owner, other) == [403, 403, 403, 403]
    assert await statuses(owner_api, owner, None) == [403, 403]
    # A hub manager and a customer have no economics at all (the franchise's business)
    assert await statuses(manager_api, manager, HUB) == [403, 403, 403, 403]
    assert await statuses(customer_api, customer, HUB) == [403, 403, 403, 403]
    # An admin reads any hub that exists, and must name one: assumptions are a hub's own
    assert await statuses(admin_api, admin, HUB) == [200, 200, 200, 200]
    assert await statuses(admin_api, admin, other) == [404, 404, 404, 404]
    assert await statuses(admin_api, admin, None) == [422, 422]
    unnamed = await admin_api.get(
        "/v1/analytics/pnl", params={"data_source": "real", "period": "2026-10"}, headers=admin
    )
    assert unnamed.json()["code"] == "request.invalid"
    # Signed out
    anonymous = clients()
    for path in ("/v1/analytics/unit-economics", "/v1/analytics/pnl", assumptions_url()):
        assert (await anonymous.get(path, params={"hub_id": str(HUB)})).status_code == 401


async def test_the_per_user_rate_limit_applies(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    valkey: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(router, "REQUESTS_PER_MINUTE", 2)
    api: httpx2.AsyncClient = clients(valkey=valkey)
    owner, _ = await as_user(api, google, SANJAY)

    first = await api.get(assumptions_url(), headers=owner)
    second = await get_pnl(api, owner)
    third = await get_ue(api, owner)

    assert [first.status_code, second.status_code, third.status_code] == [200, 200, 429]
    assert third.json()["code"] == "request.rate_limited"


# --- the assumptions ---------------------------------------------------------------------------


async def test_a_hub_that_never_saved_has_the_defaults_at_version_1(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await api.get(assumptions_url(), headers=owner)

    assert answer.status_code == 200, answer.text
    assert answer.headers["ETag"] == '"1"'
    assert answer.headers["Cache-Control"] == "private, no-cache"
    assert DEFAULT_ASSUMPTIONS == DEFAULTS
    assert answer.json() == {
        "hub_id": str(HUB),
        "assumptions": DEFAULTS,
        "version": 1,
        "history": [],
    }


async def test_put_saves_changed_keys_versions_them_and_records_history_and_audit(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)
    admin_api, admin = await signed_in(clients, google, NEHA)

    first = await api.put(
        assumptions_url(),
        json={**DEFAULTS, "aov_paise": 65_000, "cac_paise": 1_500},
        headers={**owner, "If-Match": '"1"'},
    )
    assert first.status_code == 200, first.text
    assert first.headers["ETag"] == '"2"'
    body = first.json()
    assert body["version"] == 2
    assert body["assumptions"] == {**DEFAULTS, "aov_paise": 65_000, "cac_paise": 1_500}
    assert len(body["history"]) == 1
    save = body["history"][0]
    assert save["by"] == {"name": "Sanjay Mehta"}
    assert save["changes"] == [
        {"key": "aov_paise", "from": 62_000, "to": 65_000},
        {"key": "cac_paise", "from": 1_300, "to": 1_500},
    ]

    # An admin saves on top, reading the version the owner left
    second = await admin_api.put(
        assumptions_url(),
        json={**DEFAULTS, "aov_paise": 65_000, "cac_paise": 1_500, "margin_bp": 2_600},
        headers={**admin, "If-Match": first.headers["ETag"]},
    )
    assert second.status_code == 200, second.text
    history = second.json()["history"]
    assert [h["changes"] for h in history] == [
        [{"key": "margin_bp", "from": 2_400, "to": 2_600}],  # newest first
        save["changes"],
    ]
    assert (
        history[1]["by"] == {"name": "Sanjay Mehta"} and history[0]["by"]["name"] != "Sanjay Mehta"
    )

    # The ETag is the current version, and the unit-economics page sees the saved values
    read = await api.get(assumptions_url(), headers=owner)
    assert read.headers["ETag"] == '"3"' and read.json() == second.json()
    ue = (await get_ue(api, owner, **{"from": "2026-01-05", "to": "2026-01-11"})).json()
    assert ue["assumptions"]["aov_paise"] == 65_000 and ue["used"]["margin_bp"] == 2_600

    # Rows: one per changed key, one change row each, and one audit entry per save
    rows = (
        (await db_session.execute(select(Assumption).where(Assumption.hub_id == HUB)))
        .scalars()
        .all()
    )
    assert {r.key: r.value for r in rows} == {
        "aov_paise": 65_000,
        "cac_paise": 1_500,
        "margin_bp": 2_600,
    }
    changes = (
        (await db_session.execute(select(AssumptionChange).where(AssumptionChange.hub_id == HUB)))
        .scalars()
        .all()
    )
    assert sorted((c.version, c.key) for c in changes) == [
        (2, "aov_paise"),
        (2, "cac_paise"),
        (3, "margin_bp"),
    ]
    audit = (
        (
            await db_session.execute(
                select(AuditEntry)
                .where(
                    AuditEntry.entity_type == "analytics.assumptions",
                    AuditEntry.entity_id == str(HUB),
                )
                .order_by(AuditEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert [a.action for a in audit] == ["analytics.assumptions_changed"] * 2
    assert audit[0].before == {"aov_paise": 62_000, "cac_paise": 1_300, "version": 1}
    assert audit[0].after == {"aov_paise": 65_000, "cac_paise": 1_500, "version": 2}
    assert audit[1].after == {"margin_bp": 2_600, "version": 3}
    # The defaults themselves are untouched
    default_rows = (
        (await db_session.execute(select(Assumption).where(Assumption.hub_id.is_(None))))
        .scalars()
        .all()
    )
    assert {r.key: r.value for r in default_rows} == DEFAULTS


async def test_put_with_a_stale_or_missing_if_match_is_refused_and_writes_nothing(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)
    changed = {**DEFAULTS, "aov_paise": 65_000}
    assert (
        await api.put(assumptions_url(), json=changed, headers={**owner, "If-Match": '"1"'})
    ).status_code == 200

    stale = await api.put(assumptions_url(), json=DEFAULTS, headers={**owner, "If-Match": '"1"'})
    ahead = await api.put(assumptions_url(), json=DEFAULTS, headers={**owner, "If-Match": '"9"'})
    garbled = await api.put(assumptions_url(), json=DEFAULTS, headers={**owner, "If-Match": "abc"})
    missing = await api.put(assumptions_url(), json=DEFAULTS, headers=owner)

    for refused in (stale, ahead, garbled):
        assert refused.status_code == 412
        assert refused.json()["code"] == "request.precondition_failed"
    assert missing.status_code == 428
    assert missing.json()["code"] == "request.precondition_required"
    read = (await api.get(assumptions_url(), headers=owner)).json()
    assert read["version"] == 2 and read["assumptions"]["aov_paise"] == 65_000
    assert len(read["history"]) == 1


async def test_put_that_changes_nothing_keeps_the_version(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)

    same = await api.put(assumptions_url(), json=DEFAULTS, headers={**owner, "If-Match": '"1"'})

    assert same.status_code == 200, same.text
    assert same.headers["ETag"] == '"1"'
    assert same.json()["version"] == 1 and same.json()["history"] == []
    audit = (
        (
            await db_session.execute(
                select(AuditEntry).where(AuditEntry.entity_type == "analytics.assumptions")
            )
        )
        .scalars()
        .all()
    )
    assert audit == []


async def test_put_validates_every_range(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)
    headers = {**owner, "If-Match": '"1"'}
    bad = {
        "aov_paise": 9_999,
        "margin_bp": 6_001,
        "delivery_paise": 20_001,
        "packing_paise": -1,
        "captain_paise": 5_001,
        "payment_fee_bp": 501,
        "cac_paise": 50_001,
    }

    answer = await api.put(assumptions_url(), json=bad, headers=headers)

    assert answer.status_code == 422
    body = answer.json()
    assert body["code"] == "analytics.assumptions_invalid"
    assert [e["field"] for e in body["errors"]] == list(bad)
    assert all(e["detail"] for e in body["errors"])
    # The bounds themselves are fine
    low = dict.fromkeys(DEFAULTS, 0) | {"aov_paise": 10_000}
    high = {
        "aov_paise": 500_000,
        "margin_bp": 6_000,
        "delivery_paise": 20_000,
        "packing_paise": 20_000,
        "captain_paise": 5_000,
        "payment_fee_bp": 500,
        "cac_paise": 50_000,
    }
    assert (await api.put(assumptions_url(), json=low, headers=headers)).status_code == 200
    ok = await api.put(assumptions_url(), json=high, headers={**owner, "If-Match": '"2"'})
    assert ok.status_code == 200 and ok.json()["assumptions"] == high


async def test_put_needs_every_key_and_whole_numbers(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, owner = await signed_in(clients, google, SANJAY)
    headers = {**owner, "If-Match": '"1"'}
    without = {k: v for k, v in DEFAULTS.items() if k != "cac_paise"}

    for payload in (
        without,
        {**DEFAULTS, "aov_paise": 62_000.5},
        {**DEFAULTS, "aov_paise": "62000"},
        {**DEFAULTS, "aov_paise": True},
        {**DEFAULTS, "surprise": 1},
    ):
        answer = await api.put(assumptions_url(), json=payload, headers=headers)
        assert answer.status_code == 422, payload
        assert answer.json()["code"] == "request.invalid"
    assert (await api.get(assumptions_url(), headers=owner)).json()["version"] == 1


async def test_assumptions_of_one_hub_do_not_leak_to_another_read(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """The defaults are the rows with no hub; a hub's save never rewrites them."""
    api, owner = await signed_in(clients, google, SANJAY)
    await api.put(
        assumptions_url(), json={**DEFAULTS, "cac_paise": 0}, headers={**owner, "If-Match": '"1"'}
    )

    rows = (await db_session.execute(select(Assumption))).scalars().all()

    assert {r.key: r.value for r in rows if r.hub_id is None} == DEFAULTS
    assert [(r.hub_id, r.key, r.value) for r in rows if r.hub_id is not None] == [
        (HUB, "cac_paise", 0)
    ]
