"""Refuse the synthetic-data and rollback commands while staging takes real money (ADR-0037 §4).

    infisical run --env=staging -- uv run python -m gharbasket.tools.live_guard staging-demo

`just staging-demo`, `just staging-rollback`, the simulator's `--allow-remote` and a shared-database
`just seed` call `refuse_if_live` before they write (M5 §3.10, "Guards outside the API"). A shared
database is refused when either:
- RAZORPAY_MODE=live is in the command's environment (they run under `infisical run --env=staging`);
- the database holds the cut-over's journal entry, kind `test_mode_closed`, so a variable that was
  forgotten or overridden cannot let them through.

The local Docker database is never refused and never queried: it holds no real money. If the
database cannot be read, the command is refused too (it cannot be shown to be safe). Messages name
the database host, never its URL or password.
"""

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[5]
MODE_VARIABLE = "RAZORPAY_MODE"
CUTOVER_KIND = "test_mode_closed"  # M5 §3.10: the entry that closes the test money
ADR = "ADR-0037 §4, docs/adr/0037-live-razorpay-on-staging-with-a-cut-over.md"

# What each guarded command would do to a database that takes real money.
COMMANDS = {
    "staging-demo": "loads synthetic people and money into it",
    "staging-rollback": "replaces it with an older copy, erasing rows of money that really moved",
    "seed": "writes synthetic people into it",
    "sim": "writes synthetic orders and money into it",
}


class LiveMoneyRefused(Exception):
    """A guarded command was pointed at a database that takes real money."""


async def entry_of_kind_exists(connection: AsyncConnection, kind: str) -> bool:
    """True if billing.journal_entries holds an entry of `kind`; False before M5's tables exist."""
    if await connection.scalar(text("SELECT to_regclass('billing.journal_entries')")) is None:
        return False
    found = await connection.scalar(
        text("SELECT EXISTS (SELECT 1 FROM billing.journal_entries WHERE kind = :kind)"),
        {"kind": kind},
    )
    return bool(found)


async def _cutover_recorded(url: str) -> bool:
    engine = create_engine(url, pooled=False)
    try:
        async with engine.connect() as connection:
            return await entry_of_kind_exists(connection, CUTOVER_KIND)
    finally:
        await engine.dispose()


def cutover_recorded(url: str) -> bool:
    """True once the cut-over to live mode has closed the test money on this database."""
    return asyncio.run(_cutover_recorded(url))


def refuse_if_live(command: str, url: str, environ: Mapping[str, str]) -> None:
    """Raise LiveMoneyRefused if `command` must not run against `url` (see the module docstring)."""
    if is_local_database(url):
        return
    does = COMMANDS[command]
    host = make_url(url).host or "the database"
    if environ.get(MODE_VARIABLE, "").strip().lower() == "live":
        raise LiveMoneyRefused(
            f"refused: {MODE_VARIABLE}=live in this environment, so {host} takes real money and "
            f"{command} {does} ({ADR})"
        )
    try:
        recorded = cutover_recorded(url)
    except (OSError, SQLAlchemyError) as exc:  # no answer is no proof that it is safe
        raise LiveMoneyRefused(
            f"refused: could not check {host} for a {CUTOVER_KIND} journal entry "
            f"({type(exc).__name__}), so {command} does not run ({ADR})"
        ) from None
    if recorded:
        raise LiveMoneyRefused(
            f"refused: {host} holds the {CUTOVER_KIND} journal entry, so it takes real money and "
            f"{command} {does} ({ADR})"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Exit 2 if a command must not run because the database takes real money."
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
