"""Fixtures for partner tests: the seed people, societies, households and captain partners."""

import uuid

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.tools.seed import (
    seed,
    seed_households,
    seed_partners,
    seed_societies,
    seed_trainings,
)

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import bearer, clients, google, sign_in  # noqa: F401
from ..identity.fake_firebase import FakeGoogle

MEENA = "+919800001004"  # captain partner of Sunrise Towers (seed)
SAVITA = "+919800001012"  # captain partner of Krishna Kunj (seed)
LATA = "+919800001005"  # kitchen partner role at the seed hub, no partner record yet
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
PRIYA = "+919800001001"  # customer, household in Sunrise Towers
SUNITA = "+919800001002"  # customer, household in Green Valley Heights
RAMESH = "+919800001003"  # customer, household in Krishna Kunj
ARJUN = "+919800001006"  # rider role at the seed hub, no household
NEHA = "+919800001011"  # admin


@pytest.fixture
async def partners_world(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_partners(db_session)
    await seed_trainings(db_session)


async def as_user(
    api: httpx2.AsyncClient, firebase: FakeGoogle, phone: str
) -> tuple[dict[str, str], uuid.UUID]:
    signed = await sign_in(api, firebase, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def other_hub_manager(
    api: httpx2.AsyncClient, firebase: FakeGoogle, db: AsyncSession
) -> dict[str, str]:
    """A hub manager of another hub."""
    phone = "+919800000777"
    user, _ = await find_or_create_user(db, phone)
    db.add(UserRole(user_id=user.id, role="hub_manager", scope_type="hub", scope_id=uuid.uuid4()))
    await db.flush()
    headers, _ = await as_user(api, firebase, phone)
    return headers
