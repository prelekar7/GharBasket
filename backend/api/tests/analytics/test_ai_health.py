"""`monitor_drift` scores each hub's delivery-day forecast against that day's sold lines of the
same data source, compares it with the same hub and source's backtest and flags drift per hub;
plan acceptance is scored per data source; the console reads `GET /v1/analytics/ai-health`
(M12), admins only."""

import uuid
from datetime import UTC, date, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import Household
from gharbasket.modules.intel_gateway import jobs
from gharbasket.modules.intel_gateway.models import ModelMetric
from gharbasket.modules.orders.models import Order, OrderLine, Slot
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import (
    GENERATE_AT,
    MEENA,
    NOTICE_AT,
    NOW,
    PRIYA,
    VIKRAM,
    as_user,
    subscribe,
)

pytestmark = pytest.mark.services

NEHA = "+919800001011"  # admin
SANJAY = "+919800001009"  # franchise owner of the seed hub
YESTERDAY = date(2026, 10, 9)  # NOW is Saturday 10 Oct 2026, 15:30 IST
MODEL = "forecast_lightgbm"


def test_wape_the_metric_name_and_the_labels() -> None:
    assert jobs.wape({"a": 10.0, "b": 0.0}, {"a": 8.0, "b": 2.0}) == 0.4
    assert jobs.wape({}, {"a": 3.0}) == 1.0 and jobs.wape({}, {}) == 0.0
    assert jobs.metric_model_name("forecast-lightgbm-v1") == "forecast_lightgbm"
    assert jobs.metric_model_name("forecast-autoets-v0") == "forecast_autoets"
    assert jobs.metric_model_name("rules") == "rules"
    assert jobs.label_of("synthetic", "pilot") == "synthetic"
    assert jobs.label_of("real", "pilot") == "pilot" and jobs.label_of("real", "live") == "live"
    assert jobs.ORDER_SOURCE == {"synthetic": "synthetic", "pilot": "real", "live": "real"}


async def _backtest(api: httpx2.AsyncClient, hub: uuid.UUID, source: str, value: float) -> None:
    answer = await api.post(
        "/v1/internal/intel/model-metrics",
        json={
            "items": [
                {
                    "model_name": MODEL,
                    "version": "20261004",
                    "metric": "wape",
                    "value": value,
                    "split": "backtest",
                    "data_source": source,
                    "details": {"hub_id": str(hub)},
                }
            ]
        },
    )
    assert answer.status_code == 200, answer.text


async def _forecast(
    api: httpx2.AsyncClient, hub: uuid.UUID, source: str, product: uuid.UUID, q50: float
) -> None:
    item = {
        "product_id": str(product),
        "committed": 10,
        "uncommitted_q50": q50,
        "uncommitted_qtau": q50 + 1,
        "order_qty": q50 + 11,
        "model": "LightGBM",
    }
    answer = await api.post(
        "/v1/internal/intel/forecasts",
        json={
            "hub_id": str(hub),
            "model": "forecast-lightgbm-v1",
            "data_source": source,
            "items": [
                {**item, "for_date": YESTERDAY.isoformat()},
                # the next delivery day's share is another prediction row, scored on its own day
                {**item, "for_date": "2026-10-13", "uncommitted_q50": 99},
            ],
        },
    )
    assert answer.status_code == 200, answer.text


async def _sell(
    session: AsyncSession,
    hub: uuid.UUID,
    product: uuid.UUID,
    sales: list[tuple[str, str, str, int]],  # (order kind, status, data source, qty)
) -> None:
    slot = (await session.execute(select(Slot).limit(1))).scalar_one()
    for kind, status, source, qty in sales:
        order = Order(
            household_id=uuid.uuid4(),
            hub_id=hub,
            society_id=SEED_SOCIETY_ID,
            kind=kind,
            delivery_date=YESTERDAY,
            slot_id=slot.id,
            cutoff_at=datetime(2026, 10, 8, 15, 30, tzinfo=UTC),
            status=status,
            subtotal_paise=0,
            total_paise=0,
            data_source=source,
        )
        session.add(order)
        await session.flush()
        session.add(
            OrderLine(
                order_id=order.id,
                product_id=product,
                kind="addon",  # also inside a box order: the order's kind decides
                qty=qty,
                step_qty=1,
                step_unit="piece",
                unit_price_paise=0,
            )
        )
    await session.flush()


@pytest.mark.anyio
@pytest.mark.usefixtures("delivery")
@time_machine.travel(NOW, tick=False)
async def test_two_hubs_are_scored_per_source_against_their_own_backtest(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api: httpx2.AsyncClient = clients()
    hub_a, hub_b, product = SEED_HUB_ID, uuid.uuid4(), uuid.uuid4()
    # Hub A sells 4 + 2 synthetic add-on/tiffin units (a refunded order was still bought for);
    # a cancelled add-on, a box order and a real add-on are not synthetic uncommitted demand.
    await _sell(
        db_session,
        hub_a,
        product,
        [
            ("addon", "delivered", "synthetic", 4),
            ("tiffin", "refunded", "synthetic", 2),
            ("addon", "cancelled", "synthetic", 50),
            ("box", "delivered", "synthetic", 9),
            ("addon", "delivered", "real", 30),
        ],
    )
    await _sell(db_session, hub_b, product, [("addon", "undelivered", "synthetic", 5)])
    # Backtests, oldest first: B's own (0.2), a newer B pilot one (0.01) and the newest of all,
    # A's (0.05). B compared with A's or with its pilot row would drift; with its own it does not.
    await _backtest(api, hub_b, "synthetic", 0.2)
    await _backtest(api, hub_b, "pilot", 0.01)
    await _backtest(api, hub_a, "synthetic", 0.05)
    await _forecast(api, hub_a, "synthetic", product, 2)  # 2 against 6 sold: WAPE 0.6667
    await _forecast(api, hub_b, "synthetic", product, 3.75)  # 3.75 against 5: WAPE 0.25
    await _forecast(api, hub_a, "pilot", product, 30)  # the real add-on: WAPE 0, no backtest

    body = {"scheduled_for": NOW.isoformat()}
    ran = await api.post("/v1/internal/jobs/monitor_drift", json=body)
    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"] == {
        "days": 7,
        "forecasts_scored": 3,
        "drifted": 1,
        "plans_scored": 0,
    }
    again = await api.post("/v1/internal/jobs/monitor_drift", json=body)
    assert again.json()["summary"]["forecasts_scored"] == 0  # once per hub, source and day

    live = (
        (await db_session.execute(select(ModelMetric).where(ModelMetric.split == "live")))
        .scalars()
        .all()
    )
    rows = {
        (m.details["hub_id"], m.data_source, m.metric): (m.value, m.details["backtest_wape"])
        for m in live
        if m.details
    }
    a, b = str(hub_a), str(hub_b)
    assert rows == {
        (a, "synthetic", "wape"): (0.6667, 0.05),
        (a, "synthetic", "drift"): (1.0, 0.05),
        (b, "synthetic", "wape"): (0.25, 0.2),
        (b, "synthetic", "drift"): (0.0, 0.2),
        (a, "pilot", "wape"): (0.0, None),  # no pilot backtest for A: no drift row
    }
    assert all(m.details and m.details["for_date"] == YESTERDAY.isoformat() for m in live)
    assert "mixed" not in {m.data_source for m in live}

    neha, _ = await as_user(api, google, NEHA)
    health = await api.get("/v1/analytics/ai-health", headers=neha)
    assert health.status_code == 200, health.text
    table = health.json()
    assert table["window_days"] == 7 and health.headers["Cache-Control"] == "no-store"
    model = next(m for m in table["models"] if m["model_name"] == MODEL)
    assert model["drift"] is True  # hub A drifted
    hubs = {(h["hub_id"], h["data_source"]): h for h in model["hubs"]}
    assert set(hubs) == {(a, "synthetic"), (b, "synthetic"), (a, "pilot")}
    assert (hubs[a, "synthetic"]["drift"], hubs[a, "synthetic"]["drifted_days"]) == (True, 1)
    assert hubs[b, "synthetic"]["drift"] is False and hubs[b, "synthetic"]["wape"] == 0.25
    assert hubs[b, "synthetic"]["backtest_wape"] == 0.2
    assert hubs[a, "pilot"]["drift"] is False and hubs[a, "pilot"]["backtest_wape"] is None
    backtests = {
        (m["hub_id"], m["data_source"]): m["value"]
        for m in model["metrics"]
        if m["split"] == "backtest"
    }
    assert backtests == {(b, "synthetic"): 0.2, (b, "pilot"): 0.01, (a, "synthetic"): 0.05}
    assert table["predictions"] == [{"kind": "forecast", "source": "ml", "count": 6}]

    for phone in (PRIYA, VIKRAM, SANJAY):  # customer, hub manager, franchise owner
        auth, _ = await as_user(api, google, phone)
        assert (await api.get("/v1/analytics/ai-health", headers=auth)).status_code == 403


@pytest.mark.anyio
@pytest.mark.usefixtures("delivery")
async def test_plan_acceptance_is_scored_per_data_source_from_real_cycles(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api: httpx2.AsyncClient = clients()
    with time_machine.travel(NOW, tick=False):
        priya, priya_id = await as_user(api, google, PRIYA)
        meena, _ = await as_user(api, google, MEENA)
        await subscribe(api, priya)
        await subscribe(api, meena)
        household = await identity_api.household_for_user(db_session, priya_id)
        assert household is not None  # seeded households are synthetic: Priya's becomes real
        await db_session.execute(
            update(Household).where(Household.id == household.id).values(data_source="real")
        )
        generated = await api.post(
            "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT}
        )
        assert generated.json()["summary"]["created"] == 2, generated.text
        # A generated week is a draft until its box-ready notice goes out (M3, 18:30 IST).
        notice = await api.post(
            "/v1/internal/jobs/send_box_ready_notices", json={"scheduled_for": NOTICE_AT}
        )
        assert notice.status_code == 200, notice.text
        cycle = (await api.get("/v1/cycles/current", headers=priya)).json()
        approved = await api.post(
            f"/v1/cycles/{cycle['id']}/approve",
            headers={**priya, "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'},
        )
        assert approved.status_code == 200, approved.text

    after = datetime(2026, 10, 14, 22, 3, tzinfo=UTC)  # Thursday 03:33 IST: 13 Oct is scored
    with time_machine.travel(after, tick=False):
        body = {"scheduled_for": after.isoformat()}
        ran = await api.post("/v1/internal/jobs/monitor_drift", json=body)
        again = await api.post("/v1/internal/jobs/monitor_drift", json=body)

    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"]["plans_scored"] == 2
    assert again.json()["summary"]["plans_scored"] == 0
    rows = (
        (await db_session.execute(select(ModelMetric).where(ModelMetric.metric == "acceptance")))
        .scalars()
        .all()
    )
    by_source = {(m.model_name, m.data_source): (m.value, m.details) for m in rows}
    # Priya's real week (the setting's label: pilot) was approved; Meena's synthetic one not yet.
    assert by_source == {
        ("planner_fallback", "pilot"): (
            1.0,
            {"for_date": "2026-10-13", "offered": 1, "accepted": 1},
        ),
        ("planner_fallback", "synthetic"): (
            0.0,
            {"for_date": "2026-10-13", "offered": 1, "accepted": 0},
        ),
    }
