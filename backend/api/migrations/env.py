"""Alembic environment: async, on the direct (non-pooled) database URL.

Run through `just migrate`. The URL comes from DATABASE_URL_DIRECT (falling back to DATABASE_URL),
read from the environment (Infisical) or the repository's .env file. Tests pass their own URL in
`config.attributes["database_url"]`.

The team shares one Neon database, so it is protected:
- any database other than this machine's is refused unless GB_ALLOW_REMOTE_MIGRATIONS=1, which only
  the deploy workflow sets, after the change is merged into main;
- downgrades on such a database are always refused (Postgres runs the whole migration in one
  transaction, so the refusal rolls everything back).
"""

import asyncio
import os
from collections.abc import Collection, Mapping
from importlib import import_module
from pathlib import Path
from typing import Any

from alembic import context
from alembic.runtime.migration import MigrationContext, MigrationInfo
from sqlalchemy import text
from sqlalchemy.engine import Connection

from gharbasket.modules import MODULES
from gharbasket.platform.db import LOCAL_HOSTS, SCHEMAS, Base, create_engine, is_local_database
from gharbasket.platform.logging import configure_logging
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
VERSION_SCHEMA = "platform"

config = context.config

# Every table must be registered on Base.metadata for autogenerate to see it.
import_module("gharbasket.platform.models")
for module in MODULES:
    import_module(f"gharbasket.modules.{module}.models")
target_metadata = Base.metadata
configure_logging("INFO", json=False)


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if url is None:
        settings = DatabaseSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue]
        url = settings.migrations_url
    if not url:
        raise SystemExit("Set DATABASE_URL_DIRECT or DATABASE_URL (see .env.example).")
    if not is_local_database(url) and os.environ.get("GB_ALLOW_REMOTE_MIGRATIONS") != "1":
        raise SystemExit(
            "Refusing to migrate a shared (non-local) database. Migrations reach Neon only through "
            "the deploy workflow after merge; locally use `just up` and the Docker database."
        )
    return url


def _refuse_remote_downgrade(
    ctx: MigrationContext, step: MigrationInfo, heads: Collection[Any], run_args: Mapping[str, Any]
) -> None:
    host = ctx.connection.engine.url.host if ctx.connection is not None else None
    if not step.is_upgrade and host not in LOCAL_HOSTS:
        raise SystemExit("Refusing to downgrade a shared database; write a new migration instead.")


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
        on_version_apply=_refuse_remote_downgrade,
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
