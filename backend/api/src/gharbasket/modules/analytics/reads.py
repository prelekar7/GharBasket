"""What every analytics read shares (spec §3.9): the IST day on the API's clock, the 3-second
statement timeout and the hub scope of the console roles."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit, forbidden

IST = ZoneInfo("Asia/Kolkata")
STATEMENT_TIMEOUT = "3s"
QUERY_CANCELED = "57014"  # statement_timeout, as Postgres reports it


def ist_today() -> date:
    """Today's IST calendar date on the API's own clock, so a moved clock (e2e, `just sim`) shows
    the same day everywhere."""
    return datetime.now(IST).date()


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def now() -> datetime:
    return datetime.now(UTC)


@asynccontextmanager
async def read_limit(session: AsyncSession) -> AsyncIterator[None]:
    """Analytics reads run with `SET LOCAL statement_timeout = '3s'`; a read over it is 503
    `analytics.timeout` (the console says "try a shorter range")."""
    await session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
    try:
        yield
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != QUERY_CANCELED:
            raise
        raise ProblemError(
            503, "analytics.timeout", "The read took too long. Try a shorter range."
        ) from exc


def ensure_scope(permit: Permit, hub_id: uuid.UUID | None) -> None:
    """A hub within the caller's grants (403 `auth.forbidden`); only a global role (admin) may
    leave the hub out and read every hub."""
    if hub_id is None:
        if not permit.is_global:
            raise forbidden("Give the hub you want to read.")
    else:
        permit.ensure(hub_id)
