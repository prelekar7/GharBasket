"""Fixtures for catalog tests: the seed catalog inside the test's rolled-back transaction."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import seed, seed_households, seed_societies

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import clients, google  # noqa: F401
from ..seed_cache import seed_catalog


@pytest.fixture
async def catalog(db_session: AsyncSession) -> None:
    await seed_catalog(db_session)


@pytest.fixture
async def world(db_session: AsyncSession) -> None:
    """People, societies, households and the catalog with the seed hub's prices."""
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
