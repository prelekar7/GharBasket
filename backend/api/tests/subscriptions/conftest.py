"""Fixtures for subscriptions tests: the seed world with a slot and Tuesday delivery days."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import seed_delivery

# Shared with the identity and catalog tests (pytest finds fixtures imported into a conftest).
from ..catalog.conftest import world  # noqa: F401
from ..identity.conftest import clients, google  # noqa: F401


@pytest.fixture
async def delivery(
    db_session: AsyncSession,
    world: None,  # noqa: F811 (the imported fixture, requested by name)
) -> None:
    """The seed hub's morning slot and Tuesday delivery for the seed societies."""
    await seed_delivery(db_session)
