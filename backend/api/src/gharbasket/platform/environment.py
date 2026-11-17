"""Which deployment a database belongs to: the one-row `platform.environment` mark (ADR-0036).

Every `alembic upgrade` of a shared database writes APP_ENV into it, in the migrations' own
transaction (migrations/env.py). Remote migrations run only from the deploy workflows, so staging's
database is marked `staging` by `deploy-staging.yml` and production's `prod` by its first deploy.
APP_ENV left unset counts as `prod`, as it does for the API (`Settings.app_env`): a shared database
migrated without saying which one it is gets the strict mark. A `prod` mark is never replaced by
a later run, so a staging deploy pointed at production by mistake cannot unmark it; only the owner
can, by hand (handbook 12 §12.3).

The local Docker database (and CI's) is never marked: nothing reads the mark there.
"""

from collections.abc import Mapping

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from gharbasket.platform.db import is_local_database

ENVIRONMENTS = ("dev", "test", "staging", "prod")
PRODUCTION = "prod"

_MARK = text(
    "INSERT INTO platform.environment (singleton, name) VALUES (true, :name) "
    "ON CONFLICT (singleton) DO UPDATE SET name = EXCLUDED.name, marked_at = now() "
    "WHERE platform.environment.name <> EXCLUDED.name AND platform.environment.name <> :production"
)


def environment_to_mark(url: str, environ: Mapping[str, str]) -> str | None:
    """The mark a migration run writes on `url`: APP_ENV (unset is `prod`), None on a local one."""
    if is_local_database(url):
        return None
    name = environ.get("APP_ENV", "").strip().lower() or PRODUCTION
    if name not in ENVIRONMENTS:
        raise ValueError(f"APP_ENV={name} is not one of {', '.join(ENVIRONMENTS)}")
    return name


def mark_environment(connection: Connection, name: str) -> str | None:
    """Write `name` as this database's environment and return the mark it now has (`prod` stays).

    None, and nothing written, when the table does not exist (an upgrade to an older revision).
    """
    if connection.scalar(text("SELECT to_regclass('platform.environment')")) is None:
        return None
    connection.execute(_MARK, {"name": name, "production": PRODUCTION})
    return connection.scalar(text("SELECT name FROM platform.environment"))


async def read_environment(connection: AsyncConnection) -> str | None:
    """This database's mark; None when it has none, or before the table exists."""
    if await connection.scalar(text("SELECT to_regclass('platform.environment')")) is None:
        return None
    return await connection.scalar(text("SELECT name FROM platform.environment"))
