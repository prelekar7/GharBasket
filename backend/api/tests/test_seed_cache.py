"""The replayed seeds of the fixtures leave the database as the real seeds do."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import seed_cache

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def world(session: AsyncSession) -> None:
    await seed_cache.seed(session)
    await seed_cache.seed_societies(session)
    await seed_cache.seed_households(session)
    await seed_cache.seed_catalog(session)
    await seed_cache.seed_hubs(session)
    await seed_cache.seed_partners(session)
    await seed_cache.seed_trainings(session)
    await seed_cache.seed_delivery(session)


async def test_a_replayed_world_is_the_world_the_real_seeds_made(db_session: AsyncSession) -> None:
    seed_cache._kept.clear()  # pyright: ignore[reportPrivateUsage]  (so this run is the real one)
    async with db_session.begin_nested() as real:
        await world(db_session)
        made = await seed_cache._hashes(db_session)  # pyright: ignore[reportPrivateUsage]
        await real.rollback()

    await world(db_session)  # every call is replayed

    # The hashes cover every row of every table, ids and timestamps included.
    assert await seed_cache._hashes(db_session) == made  # pyright: ignore[reportPrivateUsage]
    products = (
        await db_session.execute(text("SELECT count(*) FROM catalog.products"))
    ).scalar_one()
    assert products > 50


async def test_a_database_that_is_not_as_the_seed_found_it_gets_the_real_seed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await seed_cache.seed(db_session)  # kept, or replayed
    await db_session.rollback()  # (back to the start: no rows)
    await db_session.execute(
        text("INSERT INTO identity.users (phone_e164) VALUES ('+919800000000')")
    )

    async def replayed(*_: object) -> None:
        raise AssertionError("a seed was replayed on rows it did not find")

    monkeypatch.setattr(seed_cache, "_replay", replayed)
    await seed_cache.seed(db_session)

    users = (await db_session.execute(text("SELECT count(*) FROM identity.users"))).scalar_one()
    assert users > 1  # the stray row and the seed people
