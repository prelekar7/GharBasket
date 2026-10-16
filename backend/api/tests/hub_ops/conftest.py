"""Fixtures for hub_ops tests: the seed people (one per role), the seed hub with its vendor, and
for packing the whole seed world."""

import uuid

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import seed, seed_catalog, seed_households, seed_hubs, seed_societies

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import bearer, clients, google, sign_in  # noqa: F401
from ..identity.fake_firebase import FakeGoogle

NEHA = "+919800001011"  # admin
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
KAVITA = "+919800001007"  # packer at the seed hub
PRIYA = "+919800001001"  # customer


@pytest.fixture
async def people(db_session: AsyncSession) -> None:
    """The seed people and the seed hub, committed so failing requests do not undo them."""
    await seed(db_session)
    await seed_hubs(db_session)
    await db_session.commit()


async def as_user(api: httpx2.AsyncClient, firebase: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, firebase, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


def hub_body(slug: str = "vashi", **changes: object) -> dict[str, object]:
    return {
        "slug": slug,
        "name": "Vashi hub",
        "address": "Sector 17, Vashi, Navi Mumbai 400703",
        "location": {"lat": 19.0771, "lng": 72.9986},
        **changes,
    }


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


@pytest.fixture
async def floor(db_session: AsyncSession) -> None:
    """The seed world (people, societies, households, catalog with prices) and the seed hub."""
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await db_session.commit()
