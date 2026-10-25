"""Races of the 12:30 run on separate connections (so these commit; they clean up after
themselves): a settle or a write-off that holds its own rows while the run holds the hub and waits
for those rows. Their inserts refer to the hub, so the run's hub lock must not block foreign-key
checks, or the two deadlock (M6 §3.5, "One run per hub at a time")."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gharbasket.modules.hub_ops import receiving, service, stock, surplus
from gharbasket.modules.hub_ops.models import (
    Hub,
    InventoryLot,
    StockMove,
    SurplusOffer,
    WasteLog,
)
from gharbasket.modules.hub_ops.schemas import HubIn
from gharbasket.platform.db import create_engine
from gharbasket.platform.models import OutboxEvent

from .conftest import hub_body

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NOW = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)  # T 12:30 IST
EMPTY = {"drawn_tasks": 0, "closed_purchase_orders": 0, "offers": 0, "binned": 0}


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, pooled=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def hub(engine: AsyncEngine) -> AsyncIterator[uuid.UUID]:
    """A committed hub of its own, and afterwards everything written at it removed."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        made = await service.create_hub(
            session, HubIn.model_validate(hub_body(f"race-{uuid.uuid4().hex[:8]}"))
        )
        await session.commit()
    yield made.id
    async with AsyncSession(engine) as session:
        for model in (WasteLog, SurplusOffer, StockMove, InventoryLot):
            await session.execute(delete(model).where(model.hub_id == made.id))
        await session.execute(delete(Hub).where(Hub.id == made.id))
        await session.execute(
            delete(OutboxEvent).where(OutboxEvent.payload["hub_id"].astext == str(made.id))
        )
        await session.commit()


async def a_lot(session: AsyncSession, hub_id: uuid.UUID, qty: int) -> InventoryLot:
    """A grade C lot (always surplus) of a product the catalog does not need to know."""
    lot = InventoryLot(
        hub_id=hub_id,
        product_id=uuid.uuid4(),
        unit="kg",
        source="receipt",
        grade="C",
        qty_in=Decimal(qty),
        qty_on_hand=Decimal(0),
        received_at=NOW - timedelta(hours=8),
        best_before=NOW + timedelta(days=10),
    )
    session.add(lot)
    await session.flush()
    await receiving.move(
        session,
        hub_id=hub_id,
        product_id=lot.product_id,
        unit="kg",
        kind="receive",
        ref_id=uuid.uuid4(),
        qty=Decimal(qty),
        lot=lot,
    )
    return lot


async def run_once(engine: AsyncEngine, hub_id: uuid.UUID, held: asyncio.Event) -> dict[str, int]:
    await asyncio.wait_for(held.wait(), timeout=10)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        done = await surplus.run(session, hub_id, NOW)
        await session.commit()
        return done


async def test_a_settle_holding_its_offer_while_the_run_holds_the_hub_does_not_deadlock(
    engine: AsyncEngine, hub: uuid.UUID
) -> None:
    """The run takes the hub and waits for an expired NGO offer that a settle holds; the settle's
    bin log refers to the hub. The settle goes through, and the run finds the offer settled."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        lot = await a_lot(session, hub, 4)
        # Offered two runs ago, moved on to an NGO by the last run.
        made = await surplus.move_on(
            session, lot, lot.qty_on_hand, NOW - timedelta(days=2), offer=None
        )
        kitchen = made.offers[0]
        kitchen.status = "expired"
        ngo = (
            await surplus.move_on(session, lot, kitchen.qty, NOW - timedelta(days=1), offer=kitchen)
        ).offers[0]
        await session.commit()
    assert ngo.expires_at == NOW  # its time is up at this run
    held = asyncio.Event()

    async def settle() -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await surplus.get_offer(session, ngo.id, for_update=True)
            held.set()
            await asyncio.sleep(0.5)  # the run takes the hub meanwhile and waits for the offer
            await surplus.settle(session, ngo.id, "declined", now=NOW)
            await session.commit()

    _, done = await asyncio.wait_for(asyncio.gather(settle(), run_once(engine, hub, held)), 30)

    assert done == EMPTY
    async with AsyncSession(engine) as session:
        logs = (
            await session.execute(
                select(WasteLog.cascade_to, WasteLog.reason).where(WasteLog.offer_id == ngo.id)
            )
        ).all()
        status = await session.scalar(select(SurplusOffer.status).where(SurplusOffer.id == ngo.id))
    assert [tuple(x) for x in logs] == [("bin", "surplus")] and status == "declined"


async def test_a_write_off_holding_its_lot_while_the_run_holds_the_hub_does_not_deadlock(
    engine: AsyncEngine, hub: uuid.UUID
) -> None:
    """A write-off locks its lot and then inserts its log (which refers to the hub), as
    `waste.write_off` does; the run takes the hub and waits for that lot. The write-off goes
    through, and the run offers what is left."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        lot = await a_lot(session, hub, 5)
        await session.commit()
    held = asyncio.Event()

    async def write_off() -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            locked = await stock.get_lot(session, lot.id, for_update=True)
            held.set()
            await asyncio.sleep(0.5)  # the run takes the hub meanwhile and waits for the lot
            log = WasteLog(
                hub_id=hub,
                lot_id=locked.id,
                product_id=locked.product_id,
                qty=Decimal(2),
                unit="kg",
                kg=Decimal(2),
                reason="spoiled",
                cascade_to="bin",
            )
            session.add(log)
            await session.flush()
            await receiving.move(
                session,
                hub_id=hub,
                product_id=locked.product_id,
                unit="kg",
                kind="waste",
                ref_id=log.id,
                qty=Decimal(-2),
                lot=locked,
            )
            await session.commit()

    _, done = await asyncio.wait_for(asyncio.gather(write_off(), run_once(engine, hub, held)), 30)

    assert done == {**EMPTY, "offers": 1}
    async with AsyncSession(engine) as session:
        offered = await session.scalar(
            select(SurplusOffer.qty).where(SurplusOffer.lot_id == lot.id)
        )
        on_hand = await session.scalar(
            select(InventoryLot.qty_on_hand).where(InventoryLot.id == lot.id)
        )
        moved = await session.scalar(
            select(func.sum(StockMove.qty)).where(StockMove.lot_id == lot.id)
        )
    assert (offered, on_hand, moved) == (3, 0, 0)
