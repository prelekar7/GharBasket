"""`python -m gharbasket_training.jobs.export_snapshots` (daily 02:03 IST on the Actions runner,
ADR-0025): the module tables to `snapshots/date=<day>/` in the lake, and the point-in-time
household features as of the last order cut-off to `intel.feature_snapshots` through the API.

    uv run --directory backend/training python -m gharbasket_training.jobs.export_snapshots \\
        --out data/snapshots            # local lake folder; the bucket from the settings otherwise
        [--day 2026-10-05] [--no-features] [--push-features]

The day is a day in IST (Asia/Kolkata): today's by default, so the 02:03 IST run writes
`date=<that day>` with every row created before the run. `--day` backfills a past day with the
rows created before the end of that IST day; a future day is refused.

The features are as of the latest order cut-off at or before the run (for a backfill: at or
before the end of that day). Cut-offs are 21:00 IST every evening (M4, `orders.api.cutoff_for`),
so the 02:03 IST run uses 21:00 IST of the previous evening: the moment that night's boxes were
locked, about 5 hours earlier, not midnight UTC.

Everything is read in one REPEATABLE READ, READ ONLY transaction, so the tables and the features
come from the same moment. Reads the database with DATABASE_URL_RO (a read-only role) or
DATABASE_URL.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket_training import features, snapshots
from gharbasket_training.lake import open_lake
from gharbasket_training.settings import Settings


def plan(day: date | None, now: datetime) -> tuple[date, datetime]:
    """The partition day (IST) and the features' `as_of` for a run at `now`."""
    today = now.astimezone(snapshots.IST).date()
    day = day or today
    if day > today:
        raise ValueError(f"--day {day} is in the future (today in IST is {today})")
    return day, features.latest_cutoff(min(now, snapshots.end_of(day)))


async def run(settings: Settings, args: argparse.Namespace, now: datetime) -> dict[str, int]:
    url = settings.database_url_ro or settings.database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL_RO (or DATABASE_URL) to read the database")
    day, as_of = plan(args.day, now)
    lake = open_lake(settings, args.out)
    engine = create_engine(url.get_secret_value(), pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
            await snapshots.read_consistently(session)
            counts = await snapshots.export(session, lake, day)
            built = None if args.no_features else await features.build(session, as_of)
    finally:
        await engine.dispose()
    if built is not None:
        counts["feature_snapshots"] = len(built)
        if args.push_features:
            async with httpx2.AsyncClient() as http:
                counts["features_pushed"] = await features.push(http, settings, as_of, built)
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the module tables to the lake.")
    parser.add_argument(
        "--day", type=date.fromisoformat, help="a past IST day to backfill (default: today, IST)"
    )
    parser.add_argument("--out", type=Path, help="a local lake folder instead of the bucket")
    parser.add_argument("--no-features", action="store_true")
    parser.add_argument("--push-features", action="store_true", help="store them through the API")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    try:
        day, as_of = plan(args.day, now)
    except ValueError as e:
        parser.error(str(e))
    print(f"day {day} (IST), features as of {as_of.isoformat()}")
    counts = asyncio.run(run(Settings(), args, now))
    for name, n in counts.items():
        print(f"{name}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
