"""Fixtures for subscriptions tests: the seed world with a slot and Tuesday delivery days."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .. import seed_cache as cached

# Shared with the identity and catalog tests (pytest finds fixtures imported into a conftest).
from ..catalog.conftest import world  # noqa: F401
from ..identity.conftest import clients, google  # noqa: F401


@pytest.fixture
async def delivery(
    db_session: AsyncSession,
    world: None,  # noqa: F811 (the imported fixture, requested by name)
) -> None:
    """The seed hub's morning slot and Tuesday delivery for the seed societies."""
    await cached.seed_delivery(db_session)
