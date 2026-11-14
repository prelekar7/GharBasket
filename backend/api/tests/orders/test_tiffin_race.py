"""Two bookings of one household's tiffin for the same date and meal at the same moment, on two
real connections, book exactly one (M4 §3.5): the household's advisory lock makes the second wait
for the first's commit, then find it. On a database of its own, since the transactions commit."""

import asyncio
import os
import uuid
from datetime import timedelta

import pytest
from alembic import command
from sqlalchemy import func, make_url, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import Order
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError

from .. import seed_cache as cached
from ..conftest import LOCAL_TEST_DATABASE_URL, alembic_config, run_on_server
from .test_orders import PRIYA
from .test_tiffin_orders import tiffin_slot
from .tiffin_menus import menu

pytestmark = [pytest.mark.services]


async def outcome(coro) -> str:
    try:
        await coro
    except ProblemError as exc:
        return exc.code
    return "booked"


async def _race(url: str) -> None:
    engine = create_engine(url, pooled=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    first_day = service.now().astimezone(service.IST).date() + timedelta(days=3)
    try:
        async with sessions() as setup:  # committed, so every connection sees it
            await cached.seed(setup)
            await cached.seed_societies(setup)
            await cached.seed_households(setup)
            await cached.seed_catalog(setup)
            await tiffin_slot(setup, "lunch")
            held = (await menu(setup, first_day)).id
            free = (await menu(setup, first_day + timedelta(days=1))).id
            await setup.commit()
            owner = await setup.scalar(
                text("SELECT id FROM identity.users WHERE phone_e164 = :phone"), {"phone": PRIYA}
            )
            assert owner is not None

        # The first holds the household's lock (its order is written, not committed); the second
        # waits for it, and finds the committed tiffin instead of writing another.
        async with sessions() as one, sessions() as two:
            await orders_api.book_tiffin(one, owner, held, 1)
            waiting = asyncio.create_task(outcome(orders_api.book_tiffin(two, owner, held, 2)))
            await asyncio.sleep(0.5)
            assert not waiting.done()
            await one.commit()
            assert await waiting == "orders.tiffin_already_booked"
            await two.rollback()

        # No holding at all: two requests at once still book one.
        async def book(portions: int) -> str:
            async with sessions() as session:
                result = await outcome(orders_api.book_tiffin(session, owner, free, portions))
                await session.commit()
                return result

        results = await asyncio.gather(book(1), book(2))
        assert sorted(results) == ["booked", "orders.tiffin_already_booked"]

        async with sessions() as check:
            count = await check.scalar(
                select(func.count()).select_from(Order).where(Order.kind == "tiffin")
            )
            assert count == 2  # one per date, never two for a date and meal
    finally:
        await engine.dispose()


def test_two_bookings_of_one_meal_at_the_same_moment_book_one() -> None:
    base = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    name = f"{make_url(base).database}_tiffin_race_{uuid.uuid4().hex[:6]}"
    url = make_url(base).set(database=name).render_as_string(hide_password=False)
    run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(base, f"CREATE DATABASE {name}")
    try:
        command.upgrade(alembic_config(url), "head")
        asyncio.run(_race(url))
    finally:
        run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
