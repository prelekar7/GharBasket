"""Fixtures for catalog tests: the seed catalog inside the test's rolled-back transaction."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .. import seed_cache as cached

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import clients, google  # noqa: F401


@pytest.fixture
async def catalog(db_session: AsyncSession) -> None:
    await cached.seed_catalog(db_session)


@pytest.fixture
async def world(db_session: AsyncSession) -> None:
    """People, societies, households and the catalog with the seed hub's prices."""
    await cached.seed(db_session)
    await cached.seed_societies(db_session)
    await cached.seed_households(db_session)
    await cached.seed_catalog(db_session)
