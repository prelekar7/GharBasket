"""Refuse the synthetic-data and rollback commands on production, and on staging while it takes
real money (ADR-0036 "Guards in code", ADR-0037 §4).

    infisical run --env=staging -- uv run python -m gharbasket.tools.live_guard staging-demo

`just staging-demo`, `just staging-rollback`, the simulator's `--allow-remote` and a shared-database
`just seed` call `refuse_if_live` before they write (M5 §3.10, "Guards outside the API"). A shared
database is refused when any of these holds:
- APP_ENV=prod is in the command's environment (Infisical `prod`);
- RAZORPAY_MODE=live is in it (they run under `infisical run --env=staging`);
- the database is marked `prod` in `platform.environment`, or carries no mark at all;
- the database holds the cut-over's journal entry, kind `test_mode_closed`.
The two database checks mean a variable that was forgotten or overridden cannot let them through.

Why a mark in the database. ADR-0036 asks for a refusal by Neon project id, but the project id is
not in a connection URL (only the endpoint `ep-…` is) and finding it needs the Neon API and its key,
which the seed and the simulator do not have. A deny-list of production hosts in a variable would
protect only the shells that carry it. So the database says what it is: every `alembic upgrade` of a
shared database writes APP_ENV into `platform.environment` (`gharbasket.platform.environment`), the
staging deploy `staging` and the production deploy `prod` (unset is `prod`, as for the API), and a
`prod` mark is never replaced. It travels with Neon branches, restores and dumps, needs nobody to
remember a step, and an unmarked shared database is refused, since it cannot be shown not to be
production (a staging redeploy marks it again, e.g. after a rollback to an older restore point).

The local Docker database is never refused and never queried: it holds no real money. If the
database cannot be read, the command is refused too (it cannot be shown to be safe). Messages name
the database host, never its URL or password.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.environment import PRODUCTION, read_environment
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[5]
MODE_VARIABLE = "RAZORPAY_MODE"
ENVIRONMENT_VARIABLE = "APP_ENV"
MARK = "platform.environment"
CUTOVER_KIND = "test_mode_closed"  # M5 §3.10: the entry that closes the test money
ADR = "ADR-0037 §4, docs/adr/0037-live-razorpay-on-staging-with-a-cut-over.md"
PRODUCTION_ADR = "ADR-0036, docs/adr/0036-production-environment-for-the-pilot.md"

# What each guarded command would do to production, or to a database that takes real money.
COMMANDS = {
    "staging-demo": "loads synthetic people and money into it",
    "staging-rollback": "replaces it with an older copy, erasing rows of money that really moved",
    "seed": "writes synthetic people into it",
    "sim": "writes synthetic orders and money into it",
}


class LiveMoneyRefused(Exception):
    """A guarded command was pointed at a database that takes real money."""


class ProductionRefused(LiveMoneyRefused):
    """A guarded command was pointed at production, or at a database not shown to be another."""


@dataclass(frozen=True)
class DatabaseFacts:
    """What the guard reads from a shared database, on one connection."""

    environment: str | None  # its platform.environment mark; None without one
    cutover: bool  # it holds the cut-over's test_mode_closed entry


async def entry_of_kind_exists(connection: AsyncConnection, kind: str) -> bool:
    """True if billing.journal_entries holds an entry of `kind`; False before M5's tables exist."""
    if await connection.scalar(text("SELECT to_regclass('billing.journal_entries')")) is None:
        return False
    found = await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM billing.journal_entries WHERE kind = :kind)"),
        {"kind": kind},
    )
    return bool(found)


async def _inspect_database(url: str) -> DatabaseFacts:
    engine = create_engine(url, pooled=False)
    try:
        async with engine.connect() as connection:
            return DatabaseFacts(
                environment=await read_environment(connection),
                cutover=await entry_of_kind_exists(connection, CUTOVER_KIND),
            )
    finally:
        await engine.dispose()


def inspect_database(url: str) -> DatabaseFacts:
    """The database's environment mark, and whether the cut-over closed its test money."""
    return asyncio.run(_inspect_database(url))


def refuse_if_live(command: str, url: str, environ: Mapping[str, str]) -> None:
    """Raise LiveMoneyRefused (ProductionRefused for production) if `command` must not run against
    `url` (see the module docstring)."""
    if is_local_database(url):
        return
    does = COMMANDS[command]
    host = make_url(url).host or "the database"
    if environ.get(ENVIRONMENT_VARIABLE, "").strip().lower() == PRODUCTION:
        raise ProductionRefused(
            f"refused: {ENVIRONMENT_VARIABLE}={PRODUCTION} in this environment, so {host} is "
            f"production and {command} {does} ({PRODUCTION_ADR})"
        )
    if environ.get(MODE_VARIABLE, "").strip().lower() == "live":
        raise LiveMoneyRefused(
            f"refused: {MODE_VARIABLE}=live in this environment, so {host} takes real money and "
            f"{command} {does} ({ADR})"
        )
    try:
        facts = inspect_database(url)
    except (OSError, SQLAlchemyError) as exc:  # no answer is no proof that it is safe
        raise LiveMoneyRefused(
            f"refused: could not check {host} for its {MARK} mark and a {CUTOVER_KIND} journal "
            f"entry ({type(exc).__name__}), so {command} does not run ({ADR}; {PRODUCTION_ADR})"
        ) from None
    if facts.environment == PRODUCTION:
        raise ProductionRefused(
            f"refused: {host} is marked {PRODUCTION} in {MARK}, so it is production and "
            f"{command} {does} ({PRODUCTION_ADR})"
        )
    if facts.environment is None:
        raise ProductionRefused(
            f"refused: {host} has no {MARK} mark, so it cannot be shown not to be production and "
            f"{command} does not run. The deploy's migrations write the mark: redeploy staging "
            f"(Actions, Deploy staging) and try again ({PRODUCTION_ADR})"
        )
    if facts.cutover:
        raise LiveMoneyRefused(
            f"refused: {host} holds the {CUTOVER_KIND} journal entry, so it takes real money and "
            f"{command} {does} ({ADR})"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Exit 2 if a command must not run because the database is production or takes real "
            "money."
        )
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args(argv)
    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    try:
        refuse_if_live(args.command, url, os.environ)
    except LiveMoneyRefused as refused:
        print(refused, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
