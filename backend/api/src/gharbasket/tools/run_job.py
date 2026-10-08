"""Run a scheduled job against the local database, e.g. `just job outbox_sweep`.

Deployed environments are triggered through `POST /v1/internal/jobs/<name>` instead (the jobs
workflow, or `curl` with JOBS_TOKEN), so a laptop never runs jobs on the shared database.
"""

import argparse
import asyncio
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.files import create_store
from gharbasket.platform.jobs import JOBS, run_job
from gharbasket.platform.settings import DatabaseSettings, Settings

REPO_ROOT = Path(__file__).resolve().parents[5]


async def run(database_url: str, name: str, scheduled_for: datetime | None) -> int:
    settings = Settings(_env_file=REPO_ROOT / ".env", app_env="dev")  # pyright: ignore[reportCallIssue]
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await run_job(
                session,
                name,
                settings=settings,
                scheduled_for=scheduled_for,
                storage=create_store(settings),
            )
    finally:
        await engine.dispose()
    print(json.dumps({"status": result.status, "summary": result.summary, "error": result.error}))
    return 0 if result.status == "ok" else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a scheduled job on the local database.")
    parser.add_argument("name", help="one of: " + ", ".join(sorted(JOBS)))
    parser.add_argument("--scheduled-for", type=datetime.fromisoformat, default=None)
    args = parser.parse_args(argv)

    url = DatabaseSettings(_env_file=REPO_ROOT / ".env").migrations_url  # pyright: ignore[reportCallIssue]
    if url is None:
        parser.error("set DATABASE_URL (see .env.example)")
    if not is_local_database(url):
        parser.error("jobs run on a shared database only through POST /v1/internal/jobs/<name>")
    if args.name not in JOBS:
        parser.error(f"unknown job {args.name!r}; known: {', '.join(sorted(JOBS))}")
    return asyncio.run(run(url, args.name, args.scheduled_for))


if __name__ == "__main__":
    raise SystemExit(main())
