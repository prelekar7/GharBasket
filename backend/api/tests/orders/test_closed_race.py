"""Two rows of one missed order settled at the same moment in two transactions publish
`orders.closed` exactly once (M4 §3.8): each takes the missed order's row before it refunds, so
the one that comes second sees the first's commit. On a database of its own, since the two
transactions really commit."""

import asyncio
import os
import uuid

import pytest
from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.db import create_engine

from .. import seed_cache as cached
from ..conftest import LOCAL_TEST_DATABASE_URL, alembic_config, run_on_server
from .test_carry_over import AT_THE_DOOR, MISSED, events, place, rows_of
from .test_closed import second_miss
from .test_orders import PRIYA

pytestmark = [pytest.mark.services]


async def _race(url: str) -> None:
    engine = create_engine(url, pooled=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as setup:  # committed, so both transactions see it
            await cached.seed(setup)
            await cached.seed_societies(setup)
            await cached.seed_households(setup)
            await cached.seed_catalog(setup)
            await setup.commit()
            owner = await setup.scalar(
                text("SELECT id FROM identity.users WHERE phone_e164 = :phone"), {"phone": PRIYA}
            )
            assert owner is not None
            missed = await place(setup, owner, MISSED, AT_THE_DOOR)
            await second_miss(setup, missed)
            rows = await rows_of(setup, missed)
        first, second = rows["toor-dal"].id, rows["moong-dal"].id

        async with sessions() as one, sessions() as two:
            await orders_api.refund_carry(one, first, None)  # holds the order's row until commit
            waiting = asyncio.create_task(orders_api.refund_carry(two, second, None))
            await asyncio.sleep(0.5)
            assert not waiting.done()  # the second waits for the order, not for the first row
            await one.commit()
            await waiting
            await two.commit()

        async with sessions() as check:
            closed = await events(check, "orders.closed")
            assert [e["order_id"] for e in closed] == [str(missed)]
    finally:
        await engine.dispose()


def test_two_rows_settled_at_the_same_moment_close_the_order_once() -> None:
    base = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    name = f"{make_url(base).database}_closed_race_{uuid.uuid4().hex[:6]}"
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(base, f"CREATE DATABASE {name}")
    try:
        command.upgrade(alembic_config(url), "head")
        asyncio.run(_race(url))
    finally:
        run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
