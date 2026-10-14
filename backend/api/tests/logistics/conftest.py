"""Fixtures for logistics tests: the seed people (one per role) and the seed hub."""

import uuid

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import User
from gharbasket.tools.seed import seed, seed_hubs

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import bearer, clients, google, sign_in  # noqa: F401
from ..identity.fake_firebase import FakeGoogle

NEHA = "+919800001011"  # admin
ARJUN = "+919800001006"  # rider at the seed hub
KAVITA = "+919800001007"  # packer at the seed hub
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub


@pytest.fixture
async def people(db_session: AsyncSession) -> None:
    """The seed people and the seed hub, committed so failing requests do not undo them."""
    await seed(db_session)
    await seed_hubs(db_session)
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
