"""`GET /v1/analytics/impact` (M12 §3.6) and `GET /v1/public/societies/{slug}` (§3.7) on a known
fixture: the scope of every role, an NGO coordinator's own-NGO-only report with no donor, partner
pay and societies, hub staff reaching an NGO only at their hub, the CSV, the month limits, small
numbers, and a public page that is current, cached, rate-limited and free of anything personal."""

import csv
import io
import json
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import impact, impact_router, refresh
from gharbasket.modules.analytics.models import (
    FactCommunity,
    FactHubDay,
    FactPartnerWeek,
    RefreshRun,
)
from gharbasket.modules.community import api as community_api
from gharbasket.modules.community.models import Ngo, Society
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    SEED_NGO_ID,
    SEED_SOCIETY_ID,
    seed_partners,
)

from ..community.conftest import deliver
from ..community.test_impact import donate, household_of, line, move_in, pledge
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import as_user
from .test_kpis import OTHER_HUB, PERSONAL, signed_in, walk_keys

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 3, 12, 0, tzinfo=IST)  # Tuesday
HUB = SEED_HUB_ID
ANNAPURNA, SEVA, FAR = SEED_NGO_ID, uuid.UUID(int=0xA2), uuid.UUID(int=0xA3)
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")
HH = [uuid.UUID(int=i) for i in range(1, 12)]
NOV, NOV2, OCT = date(2026, 11, 3), date(2026, 11, 2), date(2026, 10, 20)
WEEK1, WEEK2, WEEK0 = date(2026, 11, 2), date(2026, 11, 9), date(2026, 10, 26)  # Mondays

NEHA = "+919800001011"  # admin
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
ASHA = "+919800001010"  # ngo_coordinator of Annapurna
PRIYA = "+919800001001"  # customer
DONOR_TRACES = ("Sunrise", "Green Valley", "Meena", "Priya", "A-1203")


@pytest.fixture(autouse=True)
def clock() -> Iterator[None]:
    with time_machine.travel(NOW, tick=False):
        yield


def fact(source: str, kind: str, day: date, **values: Any) -> FactCommunity:
    defaults: dict[str, Any] = {
        "source": source,
        "source_id": uuid.uuid4(),
        "hub_id": HUB,
        "society_id": None,
        "ngo_id": None,
        "on_date": day,
        "kind": kind,
        "items": 0,
        "kg_tenths": 0,
        "meals": 0,
        "points": 0,
        "people_reached": None,
        "household_id": None,
        "data_source": "real",
    }
    return FactCommunity(**(defaults | values))


def donation(day: date, household: int, items: int, kg: int, **values: Any) -> FactCommunity:
    mine: dict[str, Any] = {
        "society_id": SEED_SOCIETY_ID,
        "household_id": HH[household],
        "items": items,
        "kg_tenths": kg,
    }
    return fact("donation", "donation_verified", day, **(mine | values))


def handoff(kind: str, day: date, ngo: uuid.UUID, **values: Any) -> FactCommunity:
    return fact("handoff", f"handoff_{kind}", day, ngo_id=ngo, **values)


def week(partner: int, monday: date, paise: int, **values: Any) -> FactPartnerWeek:
    defaults: dict[str, Any] = {
        "partner_id": uuid.UUID(int=0x100 + partner),
        "iso_week": monday,
        "basis": "bag_delivered",
        "hub_id": HUB,
        "kind": "kitchen",
        "units": 1,
        "amount_paise": paise,
        "data_source": "real",
    }
    return FactPartnerWeek(**(defaults | values))


def hub_day(hub: uuid.UUID, day: date, packed: int, bin_g: int) -> FactHubDay:
    return FactHubDay(
        hub_id=hub,
        on_date=day,
        packed_g=packed,
        waste_ngo_g=0,
        waste_bin_g=bin_g,
        waste_kitchen_g=0,
        waste_imperfect_g=0,
        surplus_offered_g=0,
        surplus_taken_g=0,
        data_source="real",
    )


async def seed_impact(session: AsyncSession, *, ran_at: datetime | None = NOW) -> None:
    """November 2026 at the seed hub and another, October before it, and the synthetic world."""
    for ngo_id, name, hubs in (
        (ANNAPURNA, "Annapurna Food Bank", [HUB, OTHER_HUB]),
        (SEVA, "Seva Shelter", [HUB]),
        (FAR, "Far Away Trust", [OTHER_HUB]),
    ):
        session.add(
            Ngo(
                id=ngo_id,
                name=name,
                kind="food_bank",
                hub_ids=hubs,
                accepts=["items", "food", "meals"],
                verified=True,
            )
        )
    for seeded in (await session.execute(select(Society))).scalars():
        seeded.data_source = "real"  # the seed makes its societies synthetic
    session.add_all(
        [
            # The seed hub, November: 3 verified donations from 2 households (under 5)
            donation(NOV, 0, 3, 25),
            donation(NOV, 0, 2, 0),
            donation(NOV2, 1, 4, 10, society_id=GREEN_VALLEY),
            fact("pledge", "meals_pledged", NOV, meals=14),
            fact("referral", "referral_converted", NOV, society_id=SEED_SOCIETY_ID),
            fact("referral", "referral_converted", NOV2, society_id=SEED_SOCIETY_ID),
            fact("society", "society_live", NOV, society_id=GREEN_VALLEY),
            handoff("items", NOV, ANNAPURNA, items=7, kg_tenths=30, people_reached=40),
            handoff("food", NOV, ANNAPURNA, kg_tenths=125, people_reached=20),
            handoff("meals", NOV, ANNAPURNA, meals=6, people_reached=15),
            handoff("items", NOV, SEVA, items=2, kg_tenths=5),
            # The other hub: 3 more donating households make 5 on the platform
            *(donation(NOV, h, 1, 10, hub_id=OTHER_HUB) for h in (2, 3, 4)),
            handoff(
                "items", NOV, ANNAPURNA, hub_id=OTHER_HUB, items=1, kg_tenths=10, people_reached=5
            ),
            # October
            donation(OCT, 0, 10, 50),
            handoff("food", OCT, ANNAPURNA, kg_tenths=20),
            handoff("meals", OCT, ANNAPURNA, meals=3),
            # The synthetic world, never part of a real report
            donation(NOV, 9, 99, 990, data_source="synthetic"),
            handoff("food", NOV, SEVA, kg_tenths=990, data_source="synthetic"),
            hub_day(HUB, NOV, 100_000, 1_900),
            hub_day(HUB, NOV2, 100_000, 100),
            hub_day(OTHER_HUB, NOV, 50_000, 3_000),
            # Partners paid in November's weeks: 5 kitchens at the seed hub (one paid twice), 2 at
            # the other, one unpaid, one paid only in October's last week
            *(week(i, WEEK1, 10_000 * i) for i in range(1, 6)),
            week(1, WEEK2, 10_000),
            week(6, WEEK1, 0),
            week(7, WEEK0, 5_000),
            week(8, WEEK1, 3_000, hub_id=OTHER_HUB),
            week(9, WEEK1, 4_000, hub_id=OTHER_HUB),
            week(10, WEEK1, 1, data_source="synthetic"),
        ]
    )
    await session.flush()
    if ran_at is not None:
        session.add(
            RefreshRun(
                job=refresh.REFRESH,
                scheduled_for=ran_at - timedelta(minutes=10),
                window_from=NOV - timedelta(days=3),
                window_to=NOV + timedelta(days=1),
                status="ok",
                started_at=ran_at - timedelta(minutes=10),
                finished_at=ran_at - timedelta(minutes=9),
            )
        )
    await session.flush()
    await refresh.refresh_views(session, refresh.VIEWS)


async def get_impact(
    api: httpx2.AsyncClient, auth: dict[str, str], **params: Any
) -> httpx2.Response:
    return await api.get(
        "/v1/analytics/impact", params={"month": "2026-11", **params}, headers=auth
    )


def rows_of(answer: httpx2.Response) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(answer.text)))


# --- the hub's report ---


async def test_a_hub_managers_report_on_a_known_fixture(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await get_impact(api, manager, scope="hub", hub_id=str(HUB))

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "private, max-age=60"
    body = answer.json()
    assert (body["scope"], body["hub_id"], body["ngo_id"], body["month"]) == (
        "hub",
        str(HUB),
        None,
        "2026-11",
    )
    assert body["data_source"] == "real" and body["stale"] is False and body["as_of"] is not None
    assert body["totals"] == {
        "food_saved_kg": 12.5,
        "items_donated": 9,
        "kg_donated": 3.5,
        "donations_verified": 3,
        "households_donating": None,  # two households: under 5
        "meals_donated": 14,
        "meals_served": 6,
        "people_reached": 75,  # 40 + 20 + 15, and an NGO that reported none
        "referrals_converted": 2,
        "societies_live": 1,
        "partners_earned_paise": 160_000,  # five paid kitchens; the one paid twice counts once
        "partners_paid": 5,
        "waste_g_per_100kg": 1_000,  # 2,000 g of 200 kg packed
    }
    assert body["trend"] == {
        "months": ["2026-06", "2026-07", "2026-08", "2026-09", "2026-10", "2026-11"],
        "food_saved_kg": [0, 0, 0, 0, 2.0, 12.5],
        "items_donated": [0, 0, 0, 0, 10, 9],
        "meals_served": [0, 0, 0, 0, 3, 6],
    }
    assert body["ngos"] == [
        {
            "ngo_id": str(ANNAPURNA),
            "name": "Annapurna Food Bank",
            "handoffs": 3,
            "items": 7,
            "kg": 15.5,
            "meals": 6,
            "people_reached": 75,
        },
        {
            "ngo_id": str(SEVA),
            "name": "Seva Shelter",
            "handoffs": 1,
            "items": 2,
            "kg": 0.5,
            "meals": 0,
            "people_reached": None,
        },
    ]
    by_name = {s["name"]: s for s in body["societies"]}
    assert list(by_name) == sorted(by_name)  # by name, every society of the hub
    assert set(by_name) == {
        "Green Valley Heights",
        "Krishna Kunj",
        "Panchavati Residency",
        "Shree Ganesh CHS",
        "Sunrise Towers",
    }
    assert (
        by_name["Sunrise Towers"]["items_donated"],
        by_name["Sunrise Towers"]["kg_donated"],
    ) == (
        5,
        2.5,
    )
    assert (
        by_name["Green Valley Heights"]["items_donated"],
        by_name["Green Valley Heights"]["kg_donated"],
    ) == (4, 1.0)
    assert by_name["Krishna Kunj"]["items_donated"] == 0
    assert all(s["meals_donated"] is None for s in body["societies"])
    assert "earned_paise" not in json.dumps(body["societies"])  # no pay per society
    assert not PERSONAL & set(walk_keys(body))


async def test_the_franchise_owner_and_the_active_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await get_impact(api, owner, scope="hub", hub_id=str(HUB))
    assert answer.status_code == 200, answer.text
    assert answer.json()["totals"]["items_donated"] == 9

    manager_api, manager = await signed_in(clients, google, VIKRAM)
    chosen = await get_impact(
        manager_api,
        {**manager, "X-Active-Role": f"hub_manager:{HUB}"},
        scope="hub",
        hub_id=str(HUB),
    )
    assert chosen.status_code == 200
    wrong = await get_impact(
        manager_api, {**manager, "X-Active-Role": "admin"}, scope="hub", hub_id=str(HUB)
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "auth.role_not_granted"


async def test_partner_pay_and_households_are_hidden_under_five(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    other = (await get_impact(api, admin, scope="hub", hub_id=str(OTHER_HUB))).json()["totals"]

    # Three donating households, two paid partners: both under 5
    assert other["households_donating"] is None
    assert (other["partners_earned_paise"], other["partners_paid"]) == (None, None)
    assert (other["items_donated"], other["kg_donated"], other["waste_g_per_100kg"]) == (
        3,
        3.0,
        6_000,
    )
    assert other["people_reached"] == 5 and other["food_saved_kg"] == 0


# --- the platform ---


async def test_the_platform_report_is_for_admins_and_adds_every_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    answer = await get_impact(api, admin, scope="platform")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["hub_id"], body["ngo_id"], body["societies"]) == (None, None, None)
    assert body["totals"] == {
        "food_saved_kg": 12.5,
        "items_donated": 12,
        "kg_donated": 6.5,
        "donations_verified": 6,
        "households_donating": 5,  # 2 + 3 across the hubs: enough to show
        "meals_donated": 14,
        "meals_served": 6,
        "people_reached": 80,
        "referrals_converted": 2,
        "societies_live": 1,
        "partners_earned_paise": 167_000,  # seven paid partners across both hubs
        "partners_paid": 7,
        "waste_g_per_100kg": 2_000,  # 5,000 g of 250 kg
    }
    annapurna = next(n for n in body["ngos"] if n["ngo_id"] == str(ANNAPURNA))
    assert (
        annapurna["handoffs"],
        annapurna["items"],
        annapurna["kg"],
        annapurna["people_reached"],
    ) == (
        4,
        8,
        16.5,
        80,
    )

    manager_api, manager = await signed_in(clients, google, VIKRAM)
    refused = await get_impact(manager_api, manager, scope="platform")
    assert refused.status_code == 403 and refused.json()["code"] == "auth.forbidden"


async def test_synthetic_data_is_for_admins_and_never_pooled(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    synthetic = await get_impact(api, admin, scope="hub", hub_id=str(HUB), data_source="synthetic")

    assert synthetic.status_code == 200, synthetic.text
    totals = synthetic.json()["totals"]
    assert (totals["items_donated"], totals["food_saved_kg"]) == (99, 99.0)
    assert synthetic.json()["data_source"] == "synthetic"
    real = (await get_impact(api, admin, scope="hub", hub_id=str(HUB))).json()["totals"]
    assert real["items_donated"] == 9 and real["food_saved_kg"] == 12.5
    manager_api, manager = await signed_in(clients, google, VIKRAM)
    refused = await get_impact(
        manager_api, manager, scope="hub", hub_id=str(HUB), data_source="synthetic"
    )
    assert refused.status_code == 403
    pilot = await get_impact(api, admin, scope="platform", data_source="pilot")
    assert pilot.status_code == 422  # the stored values only: real | synthetic


# --- who may read what ---


async def test_hub_scope_needs_a_hub_the_caller_manages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    elsewhere = await get_impact(api, manager, scope="hub", hub_id=str(OTHER_HUB))
    assert elsewhere.status_code == 403 and elsewhere.json()["code"] == "auth.forbidden"
    left_out = await get_impact(api, manager, scope="hub")  # a non-admin must name the hub
    assert left_out.status_code == 403 and left_out.json()["code"] == "auth.forbidden"
    no_scope = await api.get("/v1/analytics/impact", headers=manager)
    assert no_scope.status_code == 422

    admin_api, admin = await signed_in(clients, google, NEHA)
    asked = await get_impact(admin_api, admin, scope="hub")  # an admin must name it too
    assert asked.status_code == 422 and asked.json()["code"] == "request.invalid"
    asked_ngo = await get_impact(admin_api, admin, scope="ngo")
    assert asked_ngo.status_code == 422

    customer_api, customer = await signed_in(clients, google, PRIYA)
    assert (
        await get_impact(customer_api, customer, scope="hub", hub_id=str(HUB))
    ).status_code == 403
    assert (
        await customer_api.get("/v1/analytics/impact", params={"scope": "platform"})
    ).status_code == 401


# --- an NGO's report ---


async def test_an_ngo_coordinator_sees_only_what_their_ngo_received(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, coordinator = await signed_in(clients, google, ASHA)

    answer = await get_impact(api, coordinator, scope="ngo", ngo_id=str(ANNAPURNA))

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["scope"], body["ngo_id"], body["societies"]) == ("ngo", str(ANNAPURNA), None)
    assert body["totals"] == {
        "food_saved_kg": 12.5,
        "items_donated": 8,  # items it received, at both hubs
        "kg_donated": 4.0,
        "donations_verified": None,
        "households_donating": None,
        "meals_donated": None,
        "meals_served": 6,
        "people_reached": 80,
        "referrals_converted": None,
        "societies_live": None,
        "partners_earned_paise": None,
        "partners_paid": None,
        "waste_g_per_100kg": None,
    }
    assert [n["ngo_id"] for n in body["ngos"]] == [str(ANNAPURNA)]  # no other NGO
    assert body["trend"]["items_donated"] == [0, 0, 0, 0, 0, 8]
    assert body["trend"]["meals_served"] == [0, 0, 0, 0, 3, 6]
    # A donor is nowhere: no household, society, flat or name, no earnings
    assert not PERSONAL & set(walk_keys(body))
    assert "earned" not in json.dumps(body["totals"]).replace("partners_earned_paise", "")
    for trace in DONOR_TRACES:
        assert trace not in json.dumps(body), trace

    at_a_hub = await get_impact(
        api, coordinator, scope="ngo", ngo_id=str(ANNAPURNA), hub_id=str(HUB)
    )
    assert at_a_hub.json()["totals"]["items_donated"] == 7
    assert at_a_hub.json()["totals"]["people_reached"] == 75


async def test_a_coordinator_reads_only_their_own_ngo_and_nothing_wider(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, coordinator = await signed_in(clients, google, ASHA)

    for params in (
        {"scope": "ngo", "ngo_id": str(SEVA)},  # another NGO
        {"scope": "ngo", "ngo_id": str(uuid.uuid4())},  # one that does not exist
        {"scope": "ngo"},  # none named
        {"scope": "hub", "hub_id": str(HUB)},
        {"scope": "platform"},
    ):
        refused = await get_impact(api, coordinator, **params)
        assert refused.status_code == 403 and refused.json()["code"] == "auth.forbidden", params

    admin_api, admin = await signed_in(clients, google, NEHA)
    unknown = await get_impact(admin_api, admin, scope="ngo", ngo_id=str(uuid.uuid4()))
    assert unknown.status_code == 404 and unknown.json()["code"] == "community.ngo_not_found"
    seva = await get_impact(admin_api, admin, scope="ngo", ngo_id=str(SEVA))
    assert seva.status_code == 200 and seva.json()["totals"]["items_donated"] == 2


async def test_hub_staff_reach_an_ngo_only_at_hubs_they_manage_and_it_serves(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await get_impact(api, manager, scope="ngo", ngo_id=str(ANNAPURNA))

    assert answer.status_code == 200, answer.text
    totals = answer.json()["totals"]
    # Annapurna serves both hubs, but Vikram manages the seed hub only
    assert (totals["items_donated"], totals["kg_donated"], totals["people_reached"]) == (7, 3.0, 75)
    assert totals["partners_earned_paise"] is None and totals["households_donating"] is None
    assert answer.json()["societies"] is None
    far = await get_impact(api, manager, scope="ngo", ngo_id=str(FAR))  # serves the other hub only
    assert far.status_code == 403
    other = await get_impact(
        api, manager, scope="ngo", ngo_id=str(ANNAPURNA), hub_id=str(OTHER_HUB)
    )
    assert other.status_code == 403


# --- CSV ---


async def test_the_csv_has_a_row_per_month_hub_and_ngo(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await get_impact(api, manager, scope="hub", hub_id=str(HUB), format="csv")

    assert answer.status_code == 200, answer.text
    assert answer.headers["Content-Type"].startswith("text/csv")
    disposition = answer.headers["Content-Disposition"]
    assert disposition.startswith("attachment; filename=") and disposition.endswith('.csv"')
    assert "2026-11" in disposition
    rows = rows_of(answer)
    assert list(rows[0]) == list(impact.CSV_COLUMNS)
    assert len(rows) == 3  # the hub, then Annapurna and Seva
    hub = next(r for r in rows if r["ngo_id"] == "")
    assert hub == {
        "month": "2026-11",
        "hub_id": str(HUB),
        "ngo_id": "",
        "food_saved_kg": "12.5",
        "items_donated": "9",
        "kg_donated": "3.5",
        "donations_verified": "3",
        "households_donating": "",
        "meals_donated": "14",
        "meals_served": "6",
        "people_reached": "75",
        "referrals_converted": "2",
        "societies_live": "1",
        "partners_earned_paise": "160000",
        "partners_paid": "5",
        "waste_g_per_100kg": "1000",
    }
    annapurna = next(r for r in rows if r["ngo_id"] == str(ANNAPURNA))
    assert (annapurna["items_donated"], annapurna["kg_donated"], annapurna["people_reached"]) == (
        "7",
        "3.0",
        "75",
    )
    assert annapurna["donations_verified"] == annapurna["partners_earned_paise"] == ""

    admin_api, admin = await signed_in(clients, google, NEHA)
    platform = rows_of(await get_impact(admin_api, admin, scope="platform", format="csv"))
    assert len(platform) == 5  # two hubs, three NGO receipts
    assert {r["hub_id"] for r in platform} == {str(HUB), str(OTHER_HUB)}


async def test_an_ngo_coordinators_csv_has_their_rows_and_no_donor_or_pay(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, coordinator = await signed_in(clients, google, ASHA)

    answer = await get_impact(api, coordinator, scope="ngo", ngo_id=str(ANNAPURNA), format="csv")

    assert answer.status_code == 200, answer.text
    rows = rows_of(answer)
    assert {(r["hub_id"], r["ngo_id"]) for r in rows} == {
        (str(HUB), str(ANNAPURNA)),
        (str(OTHER_HUB), str(ANNAPURNA)),
    }
    for row in rows:
        for blank in (
            "donations_verified",
            "households_donating",
            "meals_donated",
            "referrals_converted",
            "societies_live",
            "partners_earned_paise",
            "partners_paid",
            "waste_g_per_100kg",
        ):
            assert row[blank] == "", blank
    for trace in DONOR_TRACES:
        assert trace not in answer.text
    refused = await get_impact(api, coordinator, scope="ngo", ngo_id=str(SEVA), format="csv")
    assert refused.status_code == 403


async def test_a_csv_stops_at_ten_thousand_rows() -> None:
    hubs = [uuid.UUID(int=i) for i in range(1, impact.MAX_CSV_ROWS + 6)]
    row: dict[str, object] = {name: 1 for name in impact.VIEW_SUMS}
    row |= {"ngo_id": None, "month": date(2026, 11, 1)}
    loaded = impact.Report(
        impact.Target("platform", None, None, None),
        "real",
        date(2026, 11, 1),
        [row | {"hub_id": hub} for hub in hubs],
        impact.Pay({}, []),
        {},
        {},
    )

    assert len(impact.csv_rows(loaded)) == impact.MAX_CSV_ROWS


# --- months ---


async def test_the_month_defaults_to_this_one_and_goes_back_24_months(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_impact(db_session)
    api, admin = await signed_in(clients, google, NEHA)

    default = await api.get(
        "/v1/analytics/impact", params={"scope": "hub", "hub_id": str(HUB)}, headers=admin
    )
    assert default.status_code == 200 and default.json()["month"] == "2026-11"
    october = await get_impact(api, admin, scope="hub", hub_id=str(HUB), month="2026-10")
    assert october.json()["totals"]["items_donated"] == 10
    assert october.json()["trend"]["months"][-1] == "2026-10"
    oldest = await get_impact(api, admin, scope="platform", month="2024-12")
    assert oldest.status_code == 200 and oldest.json()["totals"]["items_donated"] == 0

    future = await get_impact(api, admin, scope="platform", month="2026-12")
    assert future.status_code == 422 and future.json()["code"] == "analytics.range_invalid"
    old = await get_impact(api, admin, scope="platform", month="2024-11")
    assert old.status_code == 422 and old.json()["code"] == "analytics.range_too_long"
    for bad in ("2026-13", "2026-1", "November"):
        assert (await get_impact(api, admin, scope="platform", month=bad)).status_code == 422
    assert (await get_impact(api, admin, scope="platform", format="pdf")).status_code == 422


async def test_as_of_and_stale_follow_the_last_run(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, admin = await signed_in(clients, google, NEHA)
    never = (await get_impact(api, admin, scope="platform")).json()
    assert (never["as_of"], never["stale"]) == (None, True)

    await seed_impact(db_session, ran_at=NOW - timedelta(minutes=50))
    old = (await get_impact(api, admin, scope="platform")).json()
    assert old["stale"] is True and old["as_of"] is not None


async def test_the_per_user_rate_limit(
    clients: ClientFactory,
    google: FakeGoogle,
    valkey: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(impact_router, "REQUESTS_PER_MINUTE", 2)
    api: httpx2.AsyncClient = clients(valkey=valkey)
    admin, _ = await as_user(api, google, NEHA)

    statuses = [(await get_impact(api, admin, scope="platform")).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    limited = await get_impact(api, admin, scope="platform")
    assert limited.json()["code"] == "request.rate_limited" and "Retry-After" in limited.headers


# --- the public society page ---


async def society(db: AsyncSession, society_id: uuid.UUID) -> Society:
    found = await db.get(Society, society_id)
    assert found is not None
    return found


PUBLIC_KEYS = {
    "slug",
    "name",
    "area",
    "status",
    "homes_joined",
    "unlock_target",
    "progress_bp",
    "live_since",
    "delivery_days",
    "impact",
    "join_url",
}


async def test_a_live_society_page_needs_no_sign_in_and_is_cached(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    api = clients()  # no token at all

    answer = await api.get("/v1/public/societies/sunrise-towers-kharghar")

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "public, max-age=300, stale-while-revalidate=600"
    sunrise = await society(db_session, SEED_SOCIETY_ID)
    assert sunrise.live_at is not None
    assert answer.json() == {
        "slug": "sunrise-towers-kharghar",
        "name": "Sunrise Towers",
        "area": "Sector 20, Kharghar",
        "status": "live",
        "homes_joined": None,  # two homes: under 5
        "unlock_target": 20,
        "progress_bp": 1_000,
        "live_since": sunrise.live_at.astimezone(IST).date().isoformat(),
        "delivery_days": ["tue"],
        "impact": None,
        "join_url": "https://app.gharbasket.in/join?s=sunrise-towers-kharghar",
    }


async def test_an_unlocking_society_shows_progress_and_no_delivery_days(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    api = clients(app_web_url="https://app.example.in/")

    answer = await api.get("/v1/public/societies/green-valley-heights-kharghar")

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert set(body) == PUBLIC_KEYS
    assert (body["status"], body["live_since"], body["delivery_days"], body["impact"]) == (
        "unlocking",
        None,
        [],
        None,
    )
    assert (body["homes_joined"], body["unlock_target"], body["progress_bp"]) == (None, 25, 400)
    assert body["join_url"] == "https://app.example.in/join?s=green-valley-heights-kharghar"
    # The bar works under 5 homes, and stops at the target
    sunrise = await society(db_session, SEED_SOCIETY_ID)
    sunrise.unlock_threshold = 1
    await db_session.flush()
    full = await api.get("/v1/public/societies/sunrise-towers-kharghar")
    assert full.json()["progress_bp"] == 10_000


@pytest.mark.parametrize(
    "slug",
    ["no-such-society", "panchavati-residency-kamothe", "sai-darshan-apartments-panvel"],
)
async def test_unknown_and_prospect_societies_are_not_found(
    clients: ClientFactory, slug: str
) -> None:
    answer = await clients().get(f"/v1/public/societies/{slug}")

    assert answer.status_code == 404
    assert answer.json()["code"] == "community.society_not_found"
    assert "public" not in answer.headers.get("Cache-Control", "")


@pytest.mark.parametrize(
    ("slug", "society_id"),
    [
        ("sunrise-towers-kharghar", SEED_SOCIETY_ID),  # live
        ("green-valley-heights-kharghar", GREEN_VALLEY),  # unlocking
    ],
)
async def test_a_paused_society_is_not_found(
    clients: ClientFactory, db_session: AsyncSession, slug: str, society_id: uuid.UUID
) -> None:
    api = clients()
    assert (await api.get(f"/v1/public/societies/{slug}")).status_code == 200
    (await society(db_session, society_id)).status = "paused"
    await db_session.flush()
    assert (await api.get(f"/v1/public/societies/{slug}")).status_code == 404


async def test_the_page_is_the_same_with_or_without_a_referral_code(
    clients: ClientFactory,
) -> None:
    api = clients()

    plain = await api.get("/v1/public/societies/green-valley-heights-kharghar")
    coded = await api.get("/v1/public/societies/green-valley-heights-kharghar?ref=K7Q2XD")
    unknown = await api.get("/v1/public/societies/green-valley-heights-kharghar?ref=NOSUCH")

    assert plain.status_code == coded.status_code == unknown.status_code == 200
    assert plain.json() == coded.json() == unknown.json()  # a code is never confirmed or denied


async def test_homes_and_giving_show_from_five_and_never_a_captain_or_a_person(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    await seed_partners(db_session)  # Meena is Sunrise Towers' captain
    await deliver(db_session)
    captain = await community_api.captain_card(db_session, SEED_SOCIETY_ID)
    assert captain is not None and captain.display_name == "Meena tai"
    sunita = await household_of(db_session, "+919800001002")
    homes = [sunita, *[await move_in(db_session, n) for n in range(1, 5)]]  # five homes
    api = clients()
    path = "/v1/public/societies/green-valley-heights-kharghar"

    # Four of the five give: not enough contributors
    for who, kg in zip(homes[:4], (2, 2, 2, 1), strict=True):
        await donate(db_session, who, [line("clothes", "kg", kg)], society_id=GREEN_VALLEY)
    four = (await api.get(path)).json()
    assert (four["homes_joined"], four["progress_bp"], four["impact"]) == (5, 2_000, None)

    # The fifth gives a meal: the totals show, and they add up the donations and the meals
    await pledge(db_session, homes[4][0], 2)
    five = (await api.get(path)).json()
    assert five["impact"] == {"items_donated": 7, "meals_donated": 2}
    text = json.dumps(five)
    assert not {"captain", "household_id", "user_id", "flat", "tower", "partner"} & walk_set(five)
    for trace in ("Meena", "tai", "Sunita", "B-1", "+91980"):
        assert trace not in text, trace
    # No earnings or hub block on the public page either
    assert not {"earned", "partners", "hub"} & set(five)


def walk_set(value: Any) -> set[str]:
    return set(walk_keys(value))


async def test_the_per_ip_rate_limit_applies(
    clients: ClientFactory, valkey: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(impact_router, "PUBLIC_REQUESTS_PER_MINUTE", 2)
    api: httpx2.AsyncClient = clients(valkey=valkey)
    path = "/v1/public/societies/sunrise-towers-kharghar"

    statuses = [(await api.get(path)).status_code for _ in range(3)]

    assert statuses == [200, 200, 429]
    limited = await api.get(path)
    assert limited.json()["code"] == "request.rate_limited" and "Retry-After" in limited.headers
