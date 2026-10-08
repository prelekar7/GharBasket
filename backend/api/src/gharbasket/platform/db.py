"""Postgres access: async SQLAlchemy on asyncpg, one engine per process, one session per request.

On Neon the app connects through the pooled URL, which is PgBouncer in transaction mode. PgBouncer
can hand each transaction to a different server connection, so asyncpg's prepared statements must
not outlive a transaction: the statement caches are off and every statement gets a unique name.
The same settings are used everywhere, so local runs behave like staging.
"""

from collections.abc import AsyncIterator, Collection
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, Request
from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from gharbasket.platform.errors import ProblemError

# Constraint names are predictable, so migrations can refer to them and autogenerate stays stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# One Postgres schema per module (handbook ch. 05). Alembic only looks at these.
SCHEMAS = (
    "platform",
    "identity",
    "catalog",
    "subscriptions",
    "orders",
    "billing",
    "hub_ops",
    "logistics",
    "partners",
    "community",
    "intel",
    "notifications",
    "analytics",
    "assistant",
)


class Base(DeclarativeBase):
    """Base class for every module's tables."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def as_dict(self, *, exclude: Collection[str] = ()) -> dict[str, Any]:
        """The row's columns by attribute name (each module's `export_for_user`, M1 §3.3)."""
        attrs = inspect(self).mapper.column_attrs
        return {attr.key: getattr(self, attr.key) for attr in attrs if attr.key not in exclude}


def asyncpg_url(raw: str) -> tuple[URL, dict[str, Any]]:
    """Turn a Postgres URL (as Neon or .env gives it) into an asyncpg URL plus connect arguments.

    asyncpg does not understand libpq's `sslmode` and `channel_binding` query parameters:
    `sslmode` becomes asyncpg's `ssl` argument, and `channel_binding` is dropped (asyncpg
    negotiates SCRAM itself).
    """
    url = make_url(raw)
    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    query["prepared_statement_cache_size"] = "0"

    connect_args: dict[str, Any] = {
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
        "timeout": 10,  # seconds to connect (a sleeping Neon compute wakes in about 1 s)
        # Client-side limit per statement: a runaway query is cancelled instead of holding a
        # pool slot. Set here, not as a server setting, because PgBouncer drops those.
        "command_timeout": 15,
    }
    if isinstance(sslmode, str) and sslmode != "disable":
        connect_args["ssl"] = sslmode

    return url.set(drivername="postgresql+asyncpg", query=query), connect_args


LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def is_local_database(raw_url: str) -> bool:
    """True for the Docker database on this machine (or CI's service container).

    Used as a guard: tests and unmerged migrations must never touch the shared Neon database.
    """
    return make_url(raw_url).host in LOCAL_HOSTS


def create_engine(raw_url: str, *, pooled: bool = True) -> AsyncEngine:
    """The app uses a small pool; one-off tools (migrations, scripts) pass `pooled=False`."""
    url, connect_args = asyncpg_url(raw_url)
    if not pooled:
        return sa_create_async_engine(url, connect_args=connect_args, poolclass=NullPool)
    return sa_create_async_engine(
        url,
        connect_args=connect_args,
        # Small pool: one Render instance, and Neon's pooler multiplexes behind it.
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        # Recycle before Neon's pooler closes idle client connections.
        pool_recycle=240,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. Services call `await session.commit()` themselves; anything not
    committed is rolled back when the request ends.

    A request that published outbox events gets them relayed right after it finishes (ADR-0021).
    """
    sessionmaker: async_sessionmaker[AsyncSession] | None = request.app.state.sessionmaker
    if sessionmaker is None:
        raise ProblemError(503, "platform.database_unavailable", "The database is not configured.")
    async with sessionmaker() as session:
        yield session
        if session.info.get("outbox_published"):
            from gharbasket.platform.outbox import relay_soon  # outbox imports models, not db

            relay_soon(sessionmaker)


DbSession = Annotated[AsyncSession, Depends(get_session)]
