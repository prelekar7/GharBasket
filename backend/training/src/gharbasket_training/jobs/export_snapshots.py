"""`python -m gharbasket_training.jobs.export_snapshots` (daily 02:00 IST on the Actions runner,
ADR-0025): the module tables to `snapshots/date=<day>/` in the lake, and the point-in-time
household features as of the run to `intel.feature_snapshots` through the API.

    uv run --directory backend/training python -m gharbasket_training.jobs.export_snapshots \\
        --out data/snapshots            # local lake folder; the bucket from the settings otherwise
        [--day 2026-10-05] [--no-features] [--push-features]

Reads the database with DATABASE_URL_RO (a read-only role) or DATABASE_URL.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from pathlib import Path

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine
from gharbasket_training import features, snapshots
from gharbasket_training.lake import open_lake
from gharbasket_training.settings import Settings


async def run(settings: Settings, args: argparse.Namespace) -> dict[str, int]:
    url = settings.database_url_ro or settings.database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL_RO (or DATABASE_URL) to read the database")
    lake = open_lake(settings, args.out)
    engine = create_engine(url.get_secret_value(), pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            counts = await snapshots.export(session, lake, args.day)
            if not args.no_features:
                as_of = datetime.combine(args.day, time(0, 0), tzinfo=UTC)
                built = await features.build(session, as_of)
                counts["feature_snapshots"] = len(built)
                if args.push_features:
                    async with httpx2.AsyncClient() as http:
                        counts["features_pushed"] = await features.push(
                            http, settings, as_of, built
                        )
    finally:
        await engine.dispose()
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the module tables to the lake.")
    parser.add_argument("--day", type=date.fromisoformat, default=datetime.now(UTC).date())
    parser.add_argument("--out", type=Path, help="a local lake folder instead of the bucket")
    parser.add_argument("--no-features", action="store_true")
    parser.add_argument("--push-features", action="store_true", help="store them through the API")
    args = parser.parse_args(argv)
    counts = asyncio.run(run(Settings(), args))
    for name, n in counts.items():
        print(f"{name}: {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
