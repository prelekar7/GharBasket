"""Fixtures for catalog tests: the seed catalog inside the test's rolled-back transaction."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import seed_catalog

# Shared with the identity tests (pytest finds fixtures imported into a conftest).
from ..identity.conftest import clients, google  # noqa: F401


@pytest.fixture
async def catalog(db_session: AsyncSession) -> None:
    await seed_catalog(db_session)
