"""Fixtures for community tests: the identity clients and fake Google, plus the seed societies."""

import uuid

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import seed, seed_households, seed_partners, seed_societies

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import bearer, clients, google, sign_in, valkey  # noqa: F401
from ..identity.fake_firebase import FakeGoogle


@pytest.fixture
async def societies(db_session: AsyncSession) -> None:
    """The six seed societies, inside the test's rolled-back transaction."""
    await seed_societies(db_session)


@pytest.fixture
async def captain_world(db_session: AsyncSession) -> None:
    """People, societies, households and the seed captains, appointed by M9 (the events
    delivered), with M1's captain role."""
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_partners(db_session)
    await deliver(db_session)


async def deliver(db: AsyncSession) -> None:
    """Relay until nothing is left: M9's events are published while M8's are delivered."""
    while (await relay(db)).sent:
        pass


async def customer(api: httpx2.AsyncClient, firebase: FakeGoogle, n: int = 1) -> dict[str, str]:
    """Headers of a signed-in customer; `n` picks a different person."""
    signed_in = await sign_in(api, firebase, phone_number=f"+91980000{n:04d}", sub=f"customer-{n}")
    return bearer(signed_in["access_token"])


async def rider_only(
    api: httpx2.AsyncClient, firebase: FakeGoogle, db: AsyncSession
) -> dict[str, str]:
    """Headers of a rider acting as rider (X-Active-Role), so the customer role doesn't count."""
    phone, hub = "+919800000900", uuid.uuid4()
    signed_in = await sign_in(api, firebase, phone_number=phone, sub="rider-uid")
    user = await db.get(User, uuid.UUID(signed_in["user"]["id"]))
    assert user is not None and user.phone_e164 == phone
    db.add(UserRole(user_id=user.id, role="rider", scope_type="hub", scope_id=hub))
    await db.flush()
    refreshed = await api.post(
        "/v1/auth/refresh", json={"refresh_token": signed_in["refresh_token"]}
    )
    return {**bearer(refreshed.json()["access_token"]), "X-Active-Role": f"rider:{hub}"}
