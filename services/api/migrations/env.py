"""Alembic environment: async, on the direct (non-pooled) database URL.

Run through `just migrate`. The URL comes from DATABASE_URL_DIRECT (falling back to DATABASE_URL),
read from the environment (Infisical) or the repository's .env file. Tests pass their own URL in
`config.attributes["database_url"]`.
"""

import asyncio
from pathlib import Path

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection

from gharbasket.platform.db import SCHEMAS, Base, create_engine
from gharbasket.platform.logging import configure_logging
from gharbasket.platform.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION_SCHEMA = "platform"

config = context.config
target_metadata = Base.metadata
configure_logging("INFO", json=False)


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if url is None:
        settings = Settings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue]
        url = settings.migrations_url
    if not url:
        raise SystemExit("Set DATABASE_URL_DIRECT or DATABASE_URL (see .env.example).")
    return url


def _include_name(name: str | None, type_: str, parent_names: object) -> bool:
    # Only our schemas: never PostGIS's tiger/topology tables or anything else in public.
    if type_ == "schema":
        return name in SCHEMAS
    return True


def _configure(connection: Connection) -> None:
    # The version table lives in "platform", which must exist before Alembic writes to it.
    connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {VERSION_SCHEMA}"))
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=VERSION_SCHEMA,
        include_schemas=True,
        include_name=_include_name,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    engine = create_engine(_database_url(), pooled=False)
    async with engine.connect() as connection:
        await connection.run_sync(_configure)
        await connection.commit()
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Offline (SQL script) mode is not used in this project.")
asyncio.run(_run_online())
