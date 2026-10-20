"""The API for the browser suite (`e2e/web`): the real app on a database of its own, with a clock
the tests can move and a way to start again from the seed.

    uv run --directory e2e/backend python live_server.py prepare   # build the seeded template once
    uv run --directory e2e/backend python live_server.py serve     # API on 127.0.0.1:8020

`prepare` builds `gharbasket_e2e_web_seed` (migrate to head, `tools/seed`), stamped with the alembic
head and a hash of the seed files, and builds it again only when the stamp changes. `serve` makes
`gharbasket_e2e_web` from that template (calling `prepare` first) and runs the app in this process
under `time_machine.travel(now, tick=True)`, one worker, no reload. Neither touches `gharbasket`,
`gharbasket_test` or `gharbasket_e2e` (the backend suite's); Valkey db 13.

The launcher mounts `/__e2e` on the app instance (product code has no such routes), all behind the
operator token (`Authorization: Bearer <JOBS_TOKEN>`):

    GET  /__e2e/clock            {now, ist, offset_seconds}
    POST /__e2e/clock {to}       move the clock forward to `to` (ISO 8601 + offset); else 409
    POST /__e2e/sweep            drain the outbox and run outbox_sweep until nothing is due
    POST /__e2e/reset            back to the seed: new database from the template, Valkey flushed,
                                 clock = real now

Clock rules: forward only (the wallet compares a Python-time `expires_at` with Postgres `now()`,
which is not moved, and the outbox backs off in Python time), so only a reset goes back.
`time_machine` does not patch `time.monotonic`, so uvicorn and asyncio keep working. Jobs are
triggered over the normal
`/v1/internal/jobs/{name}` with `scheduled_for`.

The sign-in rate limit (30 per IP and 10 per phone in 10 minutes) is two constants in
`gharbasket.modules.identity.router`; the launcher raises them in this process only.
"""

import argparse
import asyncio
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx2
import time_machine
import uvicorn
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from harness import (
    API_DIR,
    IST,
    JOBS_TOKEN,
    REPO,
    flush_valkey,
    recreate_database,
    sweep_outbox,
)
from pydantic import BaseModel, SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url

import gharbasket.modules.identity.router as identity_router
from gharbasket.main import create_app
from gharbasket.platform import outbox
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.settings import Settings

PORT = 8020
SEED_INPUTS = (REPO / "data" / "seeds", REPO / "backend/api/src/gharbasket/tools/seed.py")
# Where the apps under test run (Vite and the console's production server).
WEB_ORIGINS = ["http://localhost:5174", "http://localhost:3001"]
SIGN_INS = (100_000, 600)  # (limit, window in seconds): as good as off


@dataclass(frozen=True)
class WebConfig:
    database_url: str
    valkey_url: str
    port: int

    @classmethod
    def from_env(cls) -> "WebConfig":
        return cls(
            database_url=os.environ.get(
                "E2E_WEB_DATABASE_URL",
                "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket_e2e_web",
            ),
            valkey_url=os.environ.get("E2E_WEB_VALKEY_URL", "redis://localhost:6380/13"),
            port=int(os.environ.get("E2E_WEB_PORT", PORT)),
        )

    @property
    def template_url(self) -> str:
        return (
            make_url(self.database_url)
            .set(database=f"{self.database}_seed")
            .render_as_string(hide_password=False)
        )

    @property
    def database(self) -> str:
        return make_url(self.database_url).database or ""

    def check(self) -> None:
        """Both databases are dropped and rebuilt: only ours, only local, never a neighbour's."""
        if not is_local_database(self.database_url):
            raise SystemExit("E2E_WEB_DATABASE_URL must be the local Docker database")
        if not self.database.startswith("gharbasket_e2e_web"):
            raise SystemExit("the database name must start with gharbasket_e2e_web (it is dropped)")
        if not self.valkey_url.rstrip("/").endswith("/13"):
            raise SystemExit("E2E_WEB_VALKEY_URL must be Valkey db 13 (it is flushed)")

    def settings(self) -> Settings:
        return Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="dev",  # dev sign-in with a phone, no OTP
            log_level="WARNING",
            log_json=False,
            database_url=SecretStr(self.database_url),
            valkey_url=SecretStr(self.valkey_url),
            jobs_token=SecretStr(JOBS_TOKEN),
            web_origins=WEB_ORIGINS,
            # Nothing outside: pushes and WhatsApp skip, top-ups are dev orders, no uploads, no ML.
            firebase_project_id=None,
            fcm_service_account=None,
            razorpay_key_id=None,
            razorpay_key_secret=None,
            s3_endpoint_url=None,
            ml_service_url=None,
        )

    def sub_env(self, url: str) -> dict[str, str]:
        return {
            **os.environ,
            "DATABASE_URL": url,
            "DATABASE_URL_DIRECT": url,
            "LOG_LEVEL": "WARNING",
        }


# --- prepare: the seeded template -------------------------------------------------------------


def alembic_head() -> str:
    alembic = AlembicConfig(toml_file=API_DIR / "pyproject.toml")
    heads = ScriptDirectory.from_config(alembic).get_heads()
    assert len(heads) == 1, f"migration heads: {heads}"
    return heads[0]


def seed_stamp() -> str:
    """The alembic head and a hash of every file the seed reads or is written in."""
    digest = hashlib.sha256()
    for root in SEED_INPUTS:
        for path in sorted(root.rglob("*") if root.is_dir() else [root]):
            if path.is_file() and "__pycache__" not in path.parts:
                digest.update(str(path.relative_to(REPO)).encode())
                digest.update(path.read_bytes())
    return f"{alembic_head()}:{digest.hexdigest()[:16]}"


async def _admin(config: WebConfig) -> Any:
    admin = (
        make_url(config.database_url).set(database="postgres").render_as_string(hide_password=False)
    )
    return create_engine(admin, pooled=False)


async def stamp_of(config: WebConfig, database: str) -> str | None:
    engine = await _admin(config)
    try:
        async with engine.connect() as connection:
            row = await connection.execute(
                text(
                    "SELECT shobj_description(oid, 'pg_database') FROM pg_database "
                    "WHERE datname = :n"
                ),
                {"n": database},
            )
            return row.scalar()
    finally:
        await engine.dispose()


async def set_stamp(config: WebConfig, database: str, stamp: str) -> None:
    engine = await _admin(config)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f"COMMENT ON DATABASE \"{database}\" IS '{stamp}'"))
    finally:
        await engine.dispose()


def run(config: WebConfig, args: list[str], *, cwd: Any) -> None:
    done = subprocess.run(
        [sys.executable, "-m", *args],
        cwd=cwd,
        env=config.sub_env(config.template_url),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if done.returncode:
        raise SystemExit(f"{' '.join(args)} failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")


async def prepare(config: WebConfig) -> bool:
    """Build the seeded template unless it is already there for this alembic head and these seed
    files. Returns whether it was built."""
    config.check()
    stamp = seed_stamp()
    template = make_url(config.template_url).database or ""
    if await stamp_of(config, template) == stamp:
        print(f"{template}: up to date ({stamp})", flush=True)
        return False
    print(f"{template}: building ({stamp})...", flush=True)
    await recreate_database(config.template_url)
    run(config, ["alembic", "upgrade", "head"], cwd=API_DIR)
    run(config, ["gharbasket.tools.seed"], cwd=REPO)
    await set_stamp(config, template, stamp)
    print(f"{template}: built", flush=True)
    return True


# --- serve: the app, the clock, /__e2e ----------------------------------------------------------


class ClockIn(BaseModel):
    to: datetime


class Launcher:
    """Everything the /__e2e routes act on."""

    def __init__(self, config: WebConfig, app: FastAPI, traveller: time_machine.Traveller) -> None:
        self.config = config
        self.app = app
        self.traveller = traveller
        self._http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://e2e"
        )

    @staticmethod
    def real_now() -> datetime:
        return time_machine.escape_hatch.datetime.datetime.now(UTC)

    def clock(self) -> dict[str, Any]:
        now = datetime.now(UTC)
        return {
            "now": now.isoformat(),
            "ist": now.astimezone(IST).isoformat(),
            "offset_seconds": round((now - self.real_now()).total_seconds(), 3),
        }

    def move_to(self, when: datetime) -> None:
        # A float, not a datetime: a timezone-carrying destination also changes the process's TZ.
        self.traveller.move_to(when.timestamp(), tick=True)

    async def sweep(self) -> int:
        return await sweep_outbox(self._http, datetime.now(UTC))

    async def reset(self) -> None:
        """Back to the seed. The after-commit relays finish, the pool is closed, the database is
        dropped (connections cut) and made again from the template, Valkey is emptied."""
        await outbox.drain()
        await self.app.state.engine.dispose()
        await recreate_database(
            self.config.database_url, template=make_url(self.config.template_url).database
        )
        await flush_valkey(self.config.valkey_url)
        self.move_to(self.real_now())

    async def close(self) -> None:
        await self._http.aclose()


def operator(authorization: Annotated[str | None, Header()] = None) -> None:
    if authorization != f"Bearer {JOBS_TOKEN}":
        raise HTTPException(401, "the operator token is required")


def router(launcher: Launcher) -> APIRouter:
    routes = APIRouter(prefix="/__e2e", dependencies=[Depends(operator)])

    @routes.get("/clock")
    async def get_clock() -> dict[str, Any]:
        return launcher.clock()

    @routes.post("/clock")
    async def set_clock(body: ClockIn) -> dict[str, Any]:
        if body.to.tzinfo is None:
            raise HTTPException(422, "`to` needs a timezone offset, e.g. 2026-10-27T21:00:00+05:30")
        if body.to < datetime.now(UTC):
            raise HTTPException(409, "the clock only moves forward; POST /__e2e/reset starts again")
        launcher.move_to(body.to)
        return launcher.clock()

    @routes.post("/sweep")
    async def sweep() -> dict[str, Any]:
        return {"sent": await launcher.sweep(), **launcher.clock()}

    @routes.post("/reset")
    async def reset() -> dict[str, Any]:
        await launcher.reset()
        return launcher.clock()

    return routes


async def serve(config: WebConfig) -> None:
    config.check()
    await prepare(config)
    await recreate_database(config.database_url, template=make_url(config.template_url).database)
    await flush_valkey(config.valkey_url)

    # Two constants in the sign-in route, read on every call: raised for this process only.
    identity_router.EXCHANGE_PER_IP = SIGN_INS
    identity_router.EXCHANGE_PER_PHONE = SIGN_INS

    travel = time_machine.travel(datetime.now(UTC).timestamp(), tick=True)
    traveller = travel.start()
    app = create_app(config.settings())
    launcher = Launcher(config, app, traveller)
    app.include_router(router(launcher))
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=config.port,
            log_level="warning",
            access_log=False,
            loop="asyncio",
        )
    )
    try:
        await server.serve()
    finally:
        await launcher.close()
        travel.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=["prepare", "serve"])
    command = parser.parse_args().command
    config = WebConfig.from_env()
    if command == "prepare":
        asyncio.run(prepare(config))
    else:
        asyncio.run(serve(config))


if __name__ == "__main__":
    main()
