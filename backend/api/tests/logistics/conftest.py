"""Fixtures for logistics tests: the seed people (one per role) and the seed hub, and for route
plans the whole seed world with its captains, locked orders and vehicles."""

import uuid
from datetime import date, time

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.logistics.models import Vehicle
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as order_service
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    seed,
    seed_catalog,
    seed_households,
    seed_hubs,
    seed_partners,
    seed_societies,
)

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import bearer, clients, google, sign_in  # noqa: F401
from ..identity.fake_firebase import FakeGoogle

PRIYA = "+919800001001"  # Sunrise Towers
SUNITA = "+919800001002"  # Green Valley Heights (no captain)
RAMESH = "+919800001003"  # Krishna Kunj
MEENA = "+919800001004"  # Sunrise Towers, its captain
ARJUN = "+919800001006"  # rider at the seed hub
KAVITA = "+919800001007"  # packer at the seed hub
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
NEHA = "+919800001011"  # admin
# tomato 2 x 500 g, bhindi 2 x 250 g, onion 2 x 1 kg: 3.5 kg a box
BOX = {"tomato": (2, 2_500), "bhindi": (2, 1_000), "onion": (2, 4_000)}


@pytest.fixture
async def people(db_session: AsyncSession) -> None:
    """The seed people and the seed hub, committed so failing requests do not undo them."""
    await seed(db_session)
    await seed_hubs(db_session)
    await db_session.commit()


@pytest.fixture
async def world(db_session: AsyncSession) -> None:
    """The seed world with its captains (Sunrise Towers: Meena, Krishna Kunj: Savita)."""
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await seed_partners(db_session)
    while (await relay(db_session)).sent:
        pass
    await db_session.commit()


async def as_user(api: httpx2.AsyncClient, firebase: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, firebase, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def user_id(db: AsyncSession, phone: str) -> uuid.UUID:
    found = await db.scalar(select(User.id).where(User.phone_e164 == phone))
    assert found is not None
    return found


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


async def slot(db: AsyncSession, start: time) -> uuid.UUID:
    made = await order_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Slot {uuid.uuid4().hex[:8]}",
        kind="box",
        start=start,
        end=time(start.hour + 2),
        capacity=50,
    )
    return made.id


async def locked_order(
    db: AsyncSession,
    phone: str,
    slot_id: uuid.UUID,
    day: date,
    box: dict[str, tuple[int, int]] = BOX,
) -> uuid.UUID:
    """A box paid from the owner's wallet and locked by the cut-off; committed."""
    owner = await user_id(db, phone)
    household = await identity_api.household_for_user(db, owner)
    assert household is not None
    ids = await catalog_api.product_ids_by_slug(db, list(box))
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=household.id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            delivery_date=day,
            slot_id=slot_id,
            lines=tuple(orders_api.CycleLine(ids[s], q, p) for s, (q, p) in box.items()),
        ),
    )
    await credit_topup(
        db, owner, order.total_paise, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await billing_api.debit_for_order(db, owner, order.id, order.total_paise, day)
    await orders_api.transition(db, order.id, "locked", actor="system")
    await db.commit()
    return order.id


async def vehicle(
    db: AsyncSession,
    label: str,
    *,
    kg: int = 400,
    crates: int = 24,
    rider: uuid.UUID | None = None,
    kind: str = "cargo_3w",
) -> Vehicle:
    van = Vehicle(
        hub_id=SEED_HUB_ID,
        label=label,
        kind=kind,
        capacity_kg=kg,
        capacity_crates=crates,
        capacity_tiffins=0 if kind == "cargo_3w" else 30,
        default_rider_user_id=rider,
    )
    db.add(van)
    await db.commit()
    return van
