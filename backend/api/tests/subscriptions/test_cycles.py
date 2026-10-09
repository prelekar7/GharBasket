"""Subscription, delivery days and the weekly cycle through the API and the job (M3 §3.1)."""

import uuid
from datetime import UTC, date, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import Household
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.modules.subscriptions import planning, service
from gharbasket.modules.subscriptions.models import BoxLine, WeeklyCycle
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, seed_partners

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

PRIYA = "+919800001001"  # customer, Sunrise Towers (seed hub); Tuesday delivery
MEENA = "+919800001004"  # Jain household, same society
VIKRAM = "+919800001008"  # hub manager of the seed hub
NOW = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # Saturday 15:30 IST
GENERATE_AT = "2026-10-10T12:30:00Z"  # Sat 18:00 IST → delivery Tue 13 Oct
NOTICE_AT = "2026-10-10T13:00:00Z"  # Sat 18:30 IST: send_box_ready_notices for the same Tuesday
WEEK_TWO = date(2026, 10, 20)


async def as_user(
    api: httpx2.AsyncClient, google: FakeGoogle, phone: str
) -> tuple[dict, uuid.UUID]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def subscribe(api: httpx2.AsyncClient, auth: dict) -> dict:
    answer = await api.post(
        "/v1/subscriptions",
        json={"plan": "autopilot", "payment_mode": "wallet"},
        headers={**auth, "Idempotency-Key": f"sub-{uuid.uuid4()}"},
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


@time_machine.travel(NOW, tick=False)
async def test_one_subscription_per_household_with_pause_and_auto_approve(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)

    plans = (await api.get("/v1/plans", headers=priya)).json()
    created = await subscribe(api, priya)
    again = await api.post(
        "/v1/subscriptions", json={"plan": "autopilot"}, headers={**priya, "Idempotency-Key": "s2"}
    )
    autopay = await api.post(
        "/v1/subscriptions",
        json={"plan": "autopilot", "payment_mode": "autopay"},
        headers={**priya, "Idempotency-Key": "s3"},
    )

    assert [p["code"] for p in plans["items"]] == ["autopilot"] and plans["items"][0][
        "fee_paise"
    ] == 0
    assert (created["status"], created["payment_mode"], created["auto_approve"]) == (
        "active",
        "wallet",
        False,
    )
    assert (again.status_code, again.json()["code"]) == (409, "subscriptions.exists")
    assert autopay.json()["code"] == "subscriptions.payment_mode_unavailable"
    paused = await api.patch(
        "/v1/subscriptions/current",
        json={"paused_until": "2026-11-01", "auto_approve": True},
        headers=priya,
    )
    assert (
        paused.json()["status"],
        paused.json()["paused_until"],
        paused.json()["auto_approve"],
    ) == (
        "paused",
        "2026-11-01",
        True,
    )
    resumed = await api.patch(
        "/v1/subscriptions/current", json={"paused_until": None}, headers=priya
    )
    assert resumed.json()["status"] == "active" and resumed.json()["auto_approve"] is True
    assert (await api.get("/v1/subscriptions/current", headers=priya)).json()["id"] == created["id"]


@time_machine.travel(NOW, tick=False)
async def test_delivery_days_are_set_by_the_hub_manager_with_the_hubs_box_slots(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)

    seeded = (await api.get(f"/v1/societies/{SEED_SOCIETY_ID}/delivery-days", headers=priya)).json()
    slot_id = seeded["items"][0]["slot"]["id"]
    refused = await api.put(
        f"/v1/admin/societies/{SEED_SOCIETY_ID}/delivery-days",
        json={"items": [{"weekday": 4, "slot_id": slot_id}]},
        headers={**priya, "Idempotency-Key": "d1"},
    )
    changed = await api.put(
        f"/v1/admin/societies/{SEED_SOCIETY_ID}/delivery-days",
        json={"items": [{"weekday": 1, "slot_id": slot_id}, {"weekday": 4, "slot_id": slot_id}]},
        headers={**vikram, "Idempotency-Key": "d2"},
    )
    bad_slot = await api.put(
        f"/v1/admin/societies/{SEED_SOCIETY_ID}/delivery-days",
        json={"items": [{"weekday": 1, "slot_id": str(uuid.uuid4())}]},
        headers={**vikram, "Idempotency-Key": "d3"},
    )
    twice = await api.put(
        f"/v1/admin/societies/{SEED_SOCIETY_ID}/delivery-days",
        json={"items": [{"weekday": 1, "slot_id": slot_id}, {"weekday": 1, "slot_id": slot_id}]},
        headers={**vikram, "Idempotency-Key": "d4"},
    )

    assert [d["weekday"] for d in seeded["items"]] == [1] and seeded["items"][0]["slot"][
        "label"
    ] == "Morning 7-9"
    assert refused.status_code == 403
    assert [d["weekday"] for d in changed.json()["items"]] == [1, 4]
    assert bad_slot.json()["code"] == "subscriptions.delivery_days_invalid"
    assert twice.json()["code"] == "subscriptions.delivery_days_invalid"


@time_machine.travel(NOW, tick=False)
async def test_the_job_drafts_a_cycle_per_subscribed_household_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    await subscribe(api, priya)
    await subscribe(api, meena)
    await api.patch("/v1/subscriptions/current", json={"paused_until": "2026-12-01"}, headers=meena)
    none_yet = await api.get("/v1/cycles/current", headers=priya)

    first = await api.post(
        "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT}
    )
    second = await api.post(
        "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT}
    )

    assert none_yet.json()["code"] == "subscriptions.no_cycle"
    assert first.json()["summary"] == {
        "hubs": 1,
        "created": 1,  # Priya; Meena is paused; the three unsubscribed households are skipped
        "existing": 0,
        "skipped": 4,
        "delivery_date": "2026-10-13",
    }
    assert second.json()["summary"]["created"] == 0 and second.json()["summary"]["existing"] == 1
    cycles = (await db_session.execute(select(WeeklyCycle))).scalars().all()
    assert len(cycles) == 1
    cycle = cycles[0]
    cycle_id, household_id = cycle.id, cycle.household_id  # a failed request expires rows
    # Generated as a draft: nobody is told yet, and it cannot be approved or edited.
    assert (cycle.delivery_date, cycle.week_start, cycle.status) == (
        date(2026, 10, 13),
        date(2026, 10, 12),
        "draft",
    )
    assert cycle.cutoff_at == datetime(2026, 10, 12, 15, 30, tzinfo=UTC)
    assert cycle.plan_source == "fallback"  # no ML service in tests: the rules planner answers
    assert cycle.plan_prediction_id is not None and cycle.notice_sent_at is None
    await db_session.commit()
    early = await api.post(
        f"/v1/cycles/{cycle_id}/approve",
        headers={**priya, "Idempotency-Key": "a0", "If-Match": '"1"'},
    )
    assert (early.status_code, early.json()["code"]) == (409, "subscriptions.not_editable")

    # 18:30 IST: the box-ready notice opens it, once however often the job runs.
    noticed = await api.post(
        "/v1/internal/jobs/send_box_ready_notices", json={"scheduled_for": NOTICE_AT}
    )
    again = await api.post(
        "/v1/internal/jobs/send_box_ready_notices", json={"scheduled_for": NOTICE_AT}
    )
    assert noticed.json()["summary"] == {"sent": 1, "delivery_date": "2026-10-13"}
    assert again.json()["summary"] == {"sent": 0, "delivery_date": "2026-10-13"}
    cycle = await db_session.get(WeeklyCycle, cycle_id, populate_existing=True)
    assert cycle is not None
    assert (cycle.status, cycle.notice_sent_at, cycle.version) == ("awaiting_approval", NOW, 1)
    lines = (
        (await db_session.execute(select(BoxLine).where(BoxLine.cycle_id == cycle.id)))
        .scalars()
        .all()
    )
    assert {line.source for line in lines} == {"ai", "rule"}
    assert all(line.qty > 0 and line.unit_price_paise > 0 for line in lines)
    events = [t for (t,) in (await db_session.execute(select(OutboxEvent.event_type))).all()]
    assert events.count("subscriptions.cycle_generated") == 1
    assert events.count("subscriptions.subscription_paused") == 1

    body = (await api.get("/v1/cycles/current", headers=priya)).json()
    assert (
        body["id"] == str(cycle_id)
        and body["status"] == "awaiting_approval"
        and body["version"] == 1
    )
    assert body["slot"]["label"] == "Morning 7-9" and body["delivery_date"] == "2026-10-13"
    assert body["meal_plan"]["source"] == "fallback" and len(body["meal_plan"]["days"]) == 7
    monday_lunch = body["meal_plan"]["days"][0]["meals"][1]
    assert monday_lunch["slot"] == "lunch" and monday_lunch["name"]
    assert body["meal_plan"]["days"][6]["meals"][2]["tiffin"] is True  # Priya: Sunday no-cook
    kinds = {line["reason_code"] for line in body["lines"]}
    assert {"meal_plan", "daily_use"} <= kinds
    assert body["total_paise"] == sum(line["total_paise"] for line in body["lines"]) > 0
    ready = (
        (
            await db_session.execute(
                select(OutboxEvent.payload).where(
                    OutboxEvent.event_type == "subscriptions.box_ready"
                )
            )
        )
        .scalars()
        .all()
    )
    assert ready == [
        {
            "cycle_id": str(cycle_id),
            "household_id": str(household_id),
            "delivery_date": "2026-10-13",
            "cutoff_at": "2026-10-12T15:30:00+00:00",
            "total_paise": body["total_paise"],
        }
    ]
    assert body["budget_lock"] is None and body["order_id"] is None and body["captain"] is None
    # Priya's ₹6,000 Budget Lock is tight for a family of four: the box flags it, honestly.
    assert body["highlights"] == [
        {"code": "budget_near", "params": {}},
        {"code": "fallback_rules", "params": {}},
    ]
    same = await api.get(f"/v1/cycles/{cycle_id}", headers=priya)
    assert same.json()["id"] == body["id"] and same.headers["etag"] == '"1"'
    assert (await api.get(f"/v1/cycles/{cycle_id}", headers=meena)).json()[
        "code"
    ] == "subscriptions.no_cycle"

    ref = await subscriptions_api.cycle_for(db_session, household_id, date(2026, 10, 13))
    assert ref is not None and priya_id
    await subscriptions_api.mark_locked(db_session, cycle_id, uuid.uuid4())
    assert (await api.get("/v1/cycles/current", headers=priya)).json()["status"] == "locked"


@time_machine.travel(NOW, tick=False)
async def test_the_preview_plans_the_first_week_without_saving(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)

    preview = await api.get("/v1/cycles/preview", headers=priya)

    body = preview.json()
    assert preview.status_code == 200, preview.text
    assert body["id"] is None and body["status"] == "preview" and body["version"] == 0
    assert body["delivery_date"] == "2026-10-13" and body["week_start"] == "2026-10-12"
    assert body["lines"] and body["total_paise"] > 0
    assert (await db_session.execute(select(WeeklyCycle))).scalars().all() == []
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None and household.hub_id == SEED_HUB_ID


WEEK_TWO_AT = "2026-10-17T12:30:00Z"  # Sat 18:00 IST → delivery Tue 20 Oct


@time_machine.travel(NOW, tick=False)
async def test_a_staple_left_from_last_week_lowers_the_next_box_but_does_not_skip_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    catalog = await catalog_api.plan_catalog(db_session, SEED_HUB_ID)
    atta = catalog.product_by_slug("wheat-atta")
    assert atta is not None and atta.step_qty == 1 and atta.step_unit == "kg"
    profile = await identity_api.household_plan_profile(db_session, household.id)
    assert profile is not None

    def atta_on(box: list) -> int:
        return sum(line.qty for line in box if line.product_id == atta.id)

    # Week 2 as it would be drafted with an empty cupboard (nothing approved yet).
    async with service.http_client(None) as http:
        fresh, _, _ = await service.draft(
            db_session, http, make_settings(), profile, catalog, WEEK_TWO, record_for=None
        )
    fresh_qty = atta_on(fresh.lines)

    await api.post("/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT})
    await api.post("/v1/internal/jobs/send_box_ready_notices", json={"scheduled_for": NOTICE_AT})
    week_one = (await api.get("/v1/cycles/current", headers=priya)).json()
    drafted = sum(line["qty"] for line in week_one["lines"] if line["product_id"] == str(atta.id))
    assert drafted == fresh_qty == 3  # the week's need n is over 2 kg and at most 3 kg
    # Priya takes a kilo more than the draft, so a full step is left after a week of use.
    bought = drafted + 1
    edited = await api.patch(
        f"/v1/cycles/{week_one['id']}/lines",
        json={"ops": [{"op": "qty", "product_id": str(atta.id), "qty": bought}]},
        headers={**priya, "If-Match": '"1"'},
    )
    assert edited.status_code == 200, edited.text
    approved = await api.post(
        f"/v1/cycles/{week_one['id']}/approve",
        headers={**priya, "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"2"'},
    )
    assert approved.status_code == 200, approved.text

    history = await service.bought_recently(db_session, household.id, WEEK_TWO, catalog)
    assert history[atta.id] == [(1.0, bought * 1000.0)]  # a week ago, in grams
    produce = {p.id for p in catalog.products.values() if not planning.carries_over(p)}
    assert not produce & history.keys()  # tomatoes, onions, milk are bought fresh

    generated = await api.post(
        "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": WEEK_TWO_AT}
    )
    assert generated.json()["summary"]["created"] == 1, generated.text
    week_two = (
        (
            await db_session.execute(
                select(BoxLine)
                .join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id)
                .where(WeeklyCycle.delivery_date == WEEK_TWO)
            )
        )
        .scalars()
        .all()
    )
    # 4 kg less a week's n leaves 4000 - n (1 to 2 kg); the line asks for n minus that,
    # 2n - 4000 (0 to 2 kg): fewer steps than the empty-cupboard 3, but never none.
    assert 0 < atta_on(list(week_two)) < fresh_qty


@time_machine.travel(NOW, tick=False)
async def test_a_synthetic_household_gets_synthetic_cycles(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    await db_session.execute(
        update(Household).where(Household.id == household.id).values(data_source="synthetic")
    )

    await api.post("/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT})

    cycle = await subscriptions_api.cycle_for(db_session, household.id, date(2026, 10, 13))
    assert cycle is not None and cycle.data_source == "synthetic"  # ADR-0008


@time_machine.travel(NOW, tick=False)
async def test_the_cycle_shows_the_society_captains_card(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await seed_partners(db_session)
    while (await relay(db_session)).sent:  # M9 appoints Meena for Sunrise Towers
        pass
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)

    preview = await api.get("/v1/cycles/preview", headers=priya)

    # The card only: never her flat or phone (M9 §3.2).
    assert preview.json()["captain"] == {"display_name": "Meena tai", "photo_url": None}
