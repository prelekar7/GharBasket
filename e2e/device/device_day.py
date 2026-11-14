"""A delivery day happening TODAY on a local backend, for testing the Android app's field roles
(packer, captain, rider, customer) on a real phone.

    just device-day                  # build the day, then serve the API on 127.0.0.1:8000
    just device-day pack --leave 3   # (another terminal) pack the queue, leaving 3 bags
    uv run --directory e2e/backend python ../device/device_day.py prepare   # build, do not serve
    uv run --directory e2e/backend python ../device/device_day.py serve     # serve what is built

`run` (the default) rebuilds the database `gharbasket_device` from nothing (migrate to head,
`tools/seed`, simulated households), walks the jobs of delivery day T = today (IST) in their
spec order with `scheduled_for` set to each job's T-relative time, and stops where the morning
starts: packing tasks queued for the packer, a published route for the rider (not started), a
kitchen's lunch tiffins waiting at the pickup. Then it serves the real app on the real clock,
with Firebase sign-in verified against the public project `gharbasket-2f523` (dev sign-in works
too), CORS for the Capacitor WebView and the presigned file links on the local SeaweedFS.

How the past is reached without moving the clock of the API the phone talks to: the cut-off
(T-1 21:00) is before "now" by definition, so the customer endpoints and the auto-approve rule
(which read `service.now()`) would refuse it. While the day is being built, four module clocks
(`subscriptions.service.now`, `orders.service.now`, `partners.kitchens.now`, and
`catalog.tiffin_review.now` for the tiffin menu) read the step's time; they are put back before
the server starts, and every module that is not about a past deadline (logistics, hub
operations, packing, receiving, the wallet, the phone's sign-in) runs on the real clock
throughout. No `time_machine`, and the
database's clock is never touched. Nothing but `gharbasket_device` is written.
"""

import argparse
import asyncio
import contextlib
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import uuid
from collections.abc import Iterator
from contextlib import AsyncExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import uvicorn
from fastapi import FastAPI
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

import gharbasket.modules.catalog.tiffin_review as tiffin_review
import gharbasket.modules.identity.router as identity_router
import gharbasket.modules.orders.service as orders_service
import gharbasket.modules.partners.kitchens as kitchens
import gharbasket.modules.subscriptions.service as subs_service
from gharbasket.main import create_app
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import service as partners_service
from gharbasket.modules.registry import load_modules
from gharbasket.platform import outbox
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.settings import Settings
from gharbasket.tools.seed import CAPTAINS, PEOPLE, SEED_HUB_ID, SOCIETIES

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
API_DIR = REPO / "backend" / "api"
OUT = HERE / "out"
IST = ZoneInfo("Asia/Kolkata")

DATABASE = "gharbasket_device"  # the only database this tool ever drops or writes
VALKEY_DB = 12  # flushed; the other suites use 0, 13, 14, 15 and 16-127 (test workers)
TIFFIN_PRICE_PAISE = 12_000  # a portion on the kitchen's menu
HUB = f"/v1/hubs/{SEED_HUB_ID}"

# The seed people by the phone the app signs in with (tools/seed.py PEOPLE).
CUSTOMER = "+919800001001"  # Priya Sharma, Sunrise Towers
CAPTAIN = "+919800001004"  # Meena Jadhav, Sunrise Towers' captain
KITCHEN = "+919800001005"  # Lata Shinde, kitchen_partner role
RIDER = "+919800001006"  # Arjun Pawar
PACKER = "+919800001007"  # Kavita More
MANAGER = "+919800001008"  # Vikram Desai, hub manager
ADMIN = "+919800001011"  # Neha Iyer
CAPTAIN_2 = "+919800001012"  # Savita Naik, Krishna Kunj's captain
AUTO_CUSTOMER = "+919800001003"  # Ramesh Kulkarni, Krishna Kunj: turns auto-approve on
SEED_SUBSCRIBERS = (CUSTOMER, AUTO_CUSTOMER)  # seed households that subscribe for the day


class DeviceError(RuntimeError):
    """The day could not be built; the message says which call and what the API answered."""


def say(message: str) -> None:
    print(message, flush=True)


def ist(day: date, hour: int, minute: int = 0) -> datetime:
    """An IST wall-clock time on `day`, as UTC (what `scheduled_for` takes)."""
    return datetime.combine(day, clock_time(hour, minute), tzinfo=IST).astimezone(UTC)


def pretty(phone: str) -> str:
    return f"{phone[:3]} {phone[3:8]} {phone[8:]}"


# --- settings and the safety guard -----------------------------------------------------------


def assert_device_database(url: str) -> None:
    """The one rule everything else rests on: only the local database `gharbasket_device`."""
    if not is_local_database(url):
        raise SystemExit("DEVICE_DATABASE_URL must be the local Docker database")
    name = make_url(url).database
    if name != DATABASE:
        raise SystemExit(
            f"refusing database {name!r}: this tool drops and rebuilds only {DATABASE!r} "
            "(never gharbasket, gharbasket_test, gharbasket_e2e or gharbasket_e2e_web*)"
        )


@dataclass(frozen=True)
class DeviceConfig:
    database_url: str
    valkey_url: str
    host: str
    port: int
    s3_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    firebase_project: str
    jobs_token: str
    adoption: float
    sim_seed: int
    tiffin: bool

    @classmethod
    def from_env(cls) -> "DeviceConfig":
        env = os.environ.get
        return cls(
            database_url=env(
                "DEVICE_DATABASE_URL",
                f"postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/{DATABASE}",
            ),
            valkey_url=env("DEVICE_VALKEY_URL", f"redis://localhost:6380/{VALKEY_DB}"),
            # Loopback: dev sign-in (a phone number, no code) must not be open to the Wi-Fi.
            # `adb reverse` only needs localhost; set DEVICE_HOST=0.0.0.0 for a phone on the LAN.
            host=env("DEVICE_HOST", "127.0.0.1"),
            port=int(env("DEVICE_PORT", "8000")),
            s3_url=env("DEVICE_S3_URL", "http://localhost:8333"),
            s3_access_key=env("DEVICE_S3_ACCESS_KEY", "gharbasket"),
            s3_secret_key=env("DEVICE_S3_SECRET_KEY", "gharbasket-local-secret"),
            s3_bucket=env("DEVICE_S3_BUCKET", "gb-private"),
            firebase_project=env("DEVICE_FIREBASE_PROJECT", "gharbasket-2f523"),
            jobs_token=env("DEVICE_JOBS_TOKEN") or secrets.token_urlsafe(24),
            adoption=float(env("DEVICE_ADOPTION", "0.03")),
            sim_seed=int(env("DEVICE_SIM_SEED", "7")),
            tiffin=env("DEVICE_TIFFIN", "1") != "0",
        )

    def check(self) -> None:
        """Before anything is dropped, flushed or written."""
        assert_device_database(self.database_url)
        if not self.valkey_url.rstrip("/").endswith(f"/{VALKEY_DB}"):
            raise SystemExit(f"DEVICE_VALKEY_URL must be Valkey db {VALKEY_DB} (it is flushed)")
        if make_url(self.valkey_url).host not in ("localhost", "127.0.0.1", "::1"):
            raise SystemExit("DEVICE_VALKEY_URL must be the local Valkey")

    def settings(self, *, quiet: bool = False) -> Settings:
        return Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="dev",  # dev sign-in with a phone, no OTP, beside real Firebase tokens
            log_level="WARNING" if quiet else "INFO",
            log_json=False,
            database_url=SecretStr(self.database_url),
            valkey_url=SecretStr(self.valkey_url),
            jobs_token=SecretStr(self.jobs_token),
            # The phone signs in with Firebase test numbers: real ID tokens of this project.
            firebase_project_id=self.firebase_project,
            # The Capacitor WebView's origin (androidScheme https, or http).
            cors_allow_origins=["https://localhost", "http://localhost"],
            # Presigned links are made for localhost:8333, which `adb reverse` gives the phone.
            s3_endpoint_url=self.s3_url,
            s3_access_key=self.s3_access_key,
            s3_secret_key=SecretStr(self.s3_secret_key),
            s3_bucket_private=self.s3_bucket,
            # Nothing outside: no pushes, no Razorpay (top-ups are dev orders), no ML (fallbacks).
            fcm_service_account=None,
            razorpay_key_id=None,
            razorpay_key_secret=None,
            ml_service_url=None,
            ml_service_token=None,
            # Nor WhatsApp: run under `infisical run`, the process environment would otherwise
            # switch it on, and the every-minute send_deliveries would message the seed phones.
            whatsapp_token=None,
            whatsapp_phone_number_id=None,
            whatsapp_app_secret=None,
            whatsapp_verify_token=None,
            # Nor traces: under `infisical run` they would go to staging's Grafana Cloud.
            otel_exporter_otlp_endpoint=None,
            otel_exporter_otlp_headers=None,
        )

    @property
    def client_host(self) -> str:
        """Where this Mac reaches the server: the host it listens on, loopback for 0.0.0.0."""
        any_host = ("0.0.0.0", "::", "")  # noqa: S104 (compared, never bound here)
        return "127.0.0.1" if self.host in any_host else self.host

    @property
    def loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    def sub_env(self) -> dict[str, str]:
        return {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "DATABASE_URL_DIRECT": self.database_url,
            "LOG_LEVEL": "WARNING",
        }


# --- the database ----------------------------------------------------------------------------


async def recreate_database(url: str) -> None:
    """Drop `gharbasket_device` (connections cut) and create it empty."""
    assert_device_database(url)  # again, here, where the drop is
    admin = make_url(url).set(database="postgres").render_as_string(hide_password=False)
    engine = create_engine(admin, pooled=False)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(text(f'DROP DATABASE IF EXISTS "{DATABASE}" WITH (FORCE)'))
            await connection.execute(text(f'CREATE DATABASE "{DATABASE}"'))
    finally:
        await engine.dispose()


async def flush_valkey(url: str) -> None:
    valkey = Redis.from_url(url)
    try:
        await valkey.flushdb()
    finally:
        await valkey.aclose()


def run_module(config: DeviceConfig, args: list[str], *, cwd: Path) -> list[str]:
    done = subprocess.run(  # noqa: S603 (our own interpreter and fixed module names)
        [sys.executable, "-m", *args],
        cwd=cwd,
        env=config.sub_env(),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if done.returncode:
        raise DeviceError(f"{' '.join(args)} failed:\n{done.stdout[-3000:]}\n{done.stderr[-3000:]}")
    return done.stdout.splitlines()


async def build_database(config: DeviceConfig) -> None:
    config.check()
    say(f"database {DATABASE}: dropping and rebuilding (Valkey db {VALKEY_DB} flushed)")
    await recreate_database(config.database_url)
    await flush_valkey(config.valkey_url)
    run_module(config, ["alembic", "upgrade", "head"], cwd=API_DIR)
    seeded = run_module(config, ["gharbasket.tools.seed"], cwd=REPO)
    say(f"  migrated to head and seeded ({seeded[-1] if seeded else 'no output'})")


# --- the module clocks of the build ----------------------------------------------------------


class Clock:
    """The step's time for the module clocks that guard past deadlines (see the module
    docstring). Outside `installed()` nothing is patched."""

    def __init__(self) -> None:
        self.at: datetime | None = None

    def now(self) -> datetime:
        return self.at or datetime.now(UTC)

    @contextmanager
    def installed(self) -> Iterator["Clock"]:
        before = (subs_service.now, orders_service.now, kitchens.now, tiffin_review.now)
        subs_service.now = self.now  # type: ignore[assignment]
        orders_service.now = self.now  # type: ignore[assignment]
        kitchens.now = self.now  # type: ignore[assignment]
        tiffin_review.now = self.now  # type: ignore[assignment]
        try:
            yield self
        finally:
            subs_service.now, orders_service.now, kitchens.now, tiffin_review.now = before
            self.at = None


# --- the app and its callers -----------------------------------------------------------------


class Api:
    """The real app over ASGI (its own sessions and commits, its after-commit relay), called as
    the people of the day: dev sign-in by phone, bearer tokens."""

    def __init__(self, config: DeviceConfig) -> None:
        self.config = config
        self.tokens: dict[str, str] = {}

    async def start(self, stack: AsyncExitStack) -> None:
        self.app: FastAPI = create_app(self.config.settings(quiet=True))
        await stack.enter_async_context(self.app.router.lifespan_context(self.app))
        self.http = await stack.enter_async_context(
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=self.app),
                base_url="http://device",
                timeout=120.0,
            )
        )

    async def sign_in(self, phone: str) -> str:
        answer = await self.http.post(
            "/v1/auth/exchange",
            json={"dev_phone": phone, "device": {"platform": "web", "push_token": None}},
        )
        if answer.status_code != 200:
            raise DeviceError(f"sign in {phone}: {answer.status_code} {answer.text[:300]}")
        token = answer.json()["access_token"]
        self.tokens[phone] = token
        return token

    def forget(self) -> None:
        """The phones open the app again: new access tokens (they last 15 minutes)."""
        self.tokens.clear()

    async def call(
        self,
        method: str,
        path: str,
        who: str | None = None,
        *,
        etag: int | str | None = None,
        **kwargs: Any,
    ) -> Any:
        headers: dict[str, str] = dict(kwargs.pop("headers", {}))
        if who is not None:
            if who not in self.tokens:
                await self.sign_in(who)
            headers["Authorization"] = f"Bearer {self.tokens[who]}"
        if method in ("POST", "PUT") and "Idempotency-Key" not in headers:
            headers["Idempotency-Key"] = str(uuid.uuid4())
        if etag is not None:
            headers["If-Match"] = f'"{etag}"'
        answer = await self.http.request(method, path, headers=headers, **kwargs)
        if answer.status_code >= 300:
            raise DeviceError(f"{method} {path}: {answer.status_code} {answer.text[:500]}")
        return answer.json() if answer.content else None

    async def get(self, path: str, who: str | None = None, **kw: Any) -> Any:
        return await self.call("GET", path, who, **kw)

    async def post(self, path: str, who: str | None = None, **kw: Any) -> Any:
        return await self.call("POST", path, who, **kw)

    async def job(self, name: str, at: datetime) -> dict[str, Any]:
        """Trigger a job as the external cron does (operator token, `scheduled_for`); it must
        succeed. Returns its summary."""
        answer = await self.http.post(
            f"/v1/internal/jobs/{name}",
            json={"scheduled_for": at.isoformat()},
            headers={"Authorization": f"Bearer {self.config.jobs_token}"},
        )
        if answer.status_code != 200:
            raise DeviceError(f"job {name} at {at}: {answer.status_code} {answer.text[:500]}")
        body = answer.json()
        if body.get("status") != "ok":
            raise DeviceError(f"job {name} at {at}: {body}")
        return body["summary"]

    async def sweep(self, at: datetime) -> None:
        """The after-commit relays finish, then `outbox_sweep` runs until nothing is due."""
        await outbox.drain()
        for _ in range(10):
            summary = await self.job("outbox_sweep", at)
            if summary["sent"] == 0 and summary["failed"] == 0:
                return


# --- the day ---------------------------------------------------------------------------------


@dataclass
class Home:
    phone: str
    label: str
    cycle_id: str = ""
    order_id: str = ""
    total_paise: int = 0


@dataclass
class Day:
    """What the build learnt, for the summary."""

    t: date
    homes: list[Home] = field(default_factory=list[Home])
    purchase_orders: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    box_routes: list[str] = field(default_factory=list[str])
    tiffin_routes: list[str] = field(default_factory=list[str])
    assignment_id: str = ""
    notes: list[str] = field(default_factory=list[str])


def top_up_amount(needed_paise: int) -> int:
    """Whole rupees that cover `needed_paise`, at least the ₹100 minimum."""
    return max(10_000, math.ceil(needed_paise / 100) * 100)


async def fund(api: Api, phone: str, needed_paise: int) -> None:
    """A top-up as the app makes one: a (dev) Razorpay order, then its payment captured."""
    started = await api.post(
        "/v1/wallet/topups", phone, json={"amount_paise": top_up_amount(needed_paise)}
    )
    await api.post(
        "/v1/dev/razorpay/pay", phone, json={"razorpay_order_id": started["razorpay_order_id"]}
    )


async def set_delivery_day(api: Api, day: Day) -> None:
    """Every seed society with a hub delivers on today's weekday (as the browser suite's
    `delivery-day.js` does, through the admin API)."""
    weekday = day.t.weekday()  # 0 = Monday, as the API has it
    for society in SOCIETIES:
        if society.hub_id is None:
            continue
        current = await api.get(f"/v1/societies/{society.id}/delivery-days", ADMIN)
        if not current["items"]:
            continue
        slot = current["items"][0]["slot"]["id"]
        await api.call(
            "PUT",
            f"/v1/admin/societies/{society.id}/delivery-days",
            ADMIN,
            json={"items": [{"weekday": weekday, "slot_id": slot}]},
        )
    say(f"  every seed society delivers on {day.t:%A}s, T = {day.t}")


async def simulated_households(config: DeviceConfig, day: Day) -> int:
    """Synthetic households (the simulator's `populate` and `subscribe_all`, as `just sim
    --write-db` does before it replays weeks) so the hub has a real day's worth of boxes."""
    # The simulator imports the modules in an order of its own; load them the app's way first.
    load_modules()
    from gharbasket_sim import config as cohorts
    from gharbasket_sim import through_modules as through
    from gharbasket_sim.catalog import load_catalog
    from gharbasket_sim.households import sample
    from gharbasket_sim.run import populate

    engine = create_engine(config.database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            monday = day.t - timedelta(days=day.t.weekday())
            catalog = await load_catalog(session, SEED_HUB_ID, monday, monday + timedelta(days=6))
            homes = sample(config.sim_seed, cohorts.load(), catalog, config.adoption)
            created = await populate(session, homes)
            await through.subscribe_all(session, homes, created)
            await session.commit()
    finally:
        await engine.dispose()
    day.homes.extend(Home(h.phone, h.key) for h in homes)
    return len(homes)


async def subscribe_seed_customers(api: Api, day: Day) -> None:
    for phone in SEED_SUBSCRIBERS:
        await api.post(
            "/v1/subscriptions", phone, json={"plan": "autopilot", "payment_mode": "wallet"}
        )
    day.homes.extend(
        [Home(CUSTOMER, "Priya (customer)"), Home(AUTO_CUSTOMER, "Ramesh (auto-approve)")]
    )


async def customers_approve_and_pay(api: Api, day: Day) -> None:
    """T-2 10:00: each household opens its box, approves it (If-Match) and tops up its wallet; one
    asks for auto-approve instead and is approved by the 20:55 job."""
    api.forget()
    for home in day.homes:
        box = await api.get("/v1/cycles/current", home.phone)
        if box["delivery_date"] != day.t.isoformat() or box["status"] != "awaiting_approval":
            raise DeviceError(
                f"{home.label}: unexpected box {box['delivery_date']} {box['status']}"
            )
        home.cycle_id = box["id"]
        if home.phone == AUTO_CUSTOMER:
            await api.call(
                "PATCH", "/v1/subscriptions/current", home.phone, json={"auto_approve": True}
            )
            home.total_paise = box["total_paise"]
            await fund(api, home.phone, box["total_paise"] + 30_000)  # fees on top of the lines
            continue
        approved = await api.post(
            f"/v1/cycles/{home.cycle_id}/approve", home.phone, etag=box["version"]
        )
        home.order_id, home.total_paise = approved["order_id"], approved["total_paise"]
        tiffin = 2 * TIFFIN_PRICE_PAISE if home.phone == CUSTOMER else 0  # the tiffin she books
        await fund(api, home.phone, home.total_paise + tiffin + 10_000)


async def kitchen_setup(config: DeviceConfig, api: Api, day: Day, clock: Clock) -> None:
    """Lata (the seed's kitchen_partner) becomes a verified home kitchen with a pin, skills,
    capacity and an approved lunch menu for T, and Priya books 2 portions. Her partner record and
    kitchen row are written as `tools/seed` writes the captains' (the KYC path needs a person to
    upload documents); the menu row is the stand-in the e2e tiffin journey uses (M2 has no menu
    write). Everything else goes through the endpoints."""
    engine = create_engine(config.database_url, pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            user, _ = await find_or_create_user(session, KITCHEN)
            partner = await partners_service.verify(
                session,
                user_id=user.id,
                kind="kitchen",
                hub_id=SEED_HUB_ID,
                display_name="Lata tai",
                data_source="synthetic",
            )
            kitchen = await kitchens.create_for(session, partner)
            kitchen.hygiene_grade, kitchen.inspected_on = "A", day.t - timedelta(days=30)
            await session.commit()
            partner_id = partner.id
        clock.at = ist(day.t - timedelta(days=3), 10)
        await api.sweep(clock.at)
        mine = await api.get("/v1/kitchens/me", KITCHEN)
        await api.call(
            "PATCH",
            "/v1/kitchens/me",
            KITCHEN,
            etag=mine["version"],
            json={
                "area": "Sector 20, Kharghar",
                "address": "C-901, Sunrise Towers, Sector 20, Kharghar",
                "location": {"lat": 19.0398, "lng": 73.0678},
                "skills": ["veg"],
            },
        )
        await api.call(
            "PUT",
            "/v1/kitchen-capacity",
            KITCHEN,
            json={"date": day.t.isoformat(), "meal": "lunch", "max_portions": 10},
        )
        await api.post(
            "/v1/admin/slots",
            MANAGER,
            json={
                "hub_id": str(SEED_HUB_ID),
                "label": "Tiffin lunch 12:30-13:30",
                "kind": "tiffin",
                "meal": "lunch",
                "start": "12:30",
                "end": "13:30",
                "capacity": 50,
            },
        )
        async with AsyncSession(engine) as session:  # M2 has no endpoint for a kitchen's menu yet
            menu_id = await session.scalar(
                text(
                    """
                    INSERT INTO catalog.tiffin_menus
                      (hub_id, partner_id, date, meal, product_id, recipe_ids, price_paise, status)
                    VALUES (:hub, :partner, :day, 'lunch',
                      (SELECT id FROM catalog.products WHERE slug = 'tomato'),
                      ARRAY(SELECT id FROM catalog.recipes ORDER BY slug LIMIT 2),
                      :price, 'submitted')
                    RETURNING id
                    """
                ),
                {
                    "hub": SEED_HUB_ID,
                    "partner": partner_id,
                    "day": day.t,
                    "price": TIFFIN_PRICE_PAISE,
                },
            )
            await session.commit()
    finally:
        await engine.dispose()
    await api.post(f"/v1/tiffin-menus/{menu_id}/approve", MANAGER, json={})
    say(f"  kitchen {pretty(KITCHEN)} (Lata tai) set up, lunch menu approved")


async def book_tiffin(api: Api, day: Day) -> None:
    """Priya books 2 portions of Lata's menu (before the cut-off, on the module clock)."""
    menus = (await api.get(f"/v1/tiffin-menus?date={day.t}&meal=lunch", CUSTOMER))["items"]
    if len(menus) != 1:
        raise DeviceError(f"expected one approved lunch menu, got {menus}")
    await api.post(
        "/v1/orders", CUSTOMER, json={"kind": "tiffin", "menu_id": menus[0]["id"], "portions": 2}
    )
    say(f"  {pretty(CUSTOMER)} booked 2 tiffin portions")


async def evening_before(api: Api, day: Day, clock: Clock) -> None:
    """T-1: reminders, auto-approve, the cut-off, procurement and purchase orders."""
    eve = day.t - timedelta(days=1)

    clock.at = ist(eve, 19)
    await api.job("remind_unapproved", clock.at)
    clock.at = ist(eve, 20, 55)
    approved = await api.job("apply_auto_approve", clock.at)
    await api.sweep(clock.at)
    say(f"  T-1 20:55 auto-approve: {approved['approved']} approved")

    clock.at = ist(eve, 21)
    locked = await api.job("cutoff_lock", clock.at)
    await api.sweep(clock.at)
    say(f"  21:00 cut-off: {locked['locked']} locked, {locked['payment_failed']} payment_failed")
    if locked["payment_failed"]:
        day.notes.append(f"{locked['payment_failed']} orders failed payment at the cut-off")

    clock.at = ist(eve, 21, 10)
    built = await api.job("build_procurement_plan", clock.at)
    plan = await api.get(f"{HUB}/procurement-plans", MANAGER, params={"date": day.t.isoformat()})
    made = await api.post(f"/v1/procurement-plans/{plan['id']}/purchase-orders", MANAGER)
    for po in made["purchase_orders"]:
        sent = await api.post(f"/v1/purchase-orders/{po['id']}/send", MANAGER)
        day.purchase_orders.append(sent)
    sent_pos = len(day.purchase_orders)
    say(f"  21:10 procurement: {built['built']} plan, {sent_pos} purchase orders sent")


async def tiffin_round(api: Api, day: Day, clock: Clock) -> None:
    """T-1 21:30 the assignment round offers Priya's tiffins to Lata; she accepts."""
    eve = day.t - timedelta(days=1)
    clock.at = ist(eve, 21, 30)
    ran = await api.job("assign_kitchens", clock.at)
    await api.sweep(clock.at)
    clock.at = ist(eve, 21, 45)
    offers = (await api.get(f"/v1/assignments?date={day.t}", KITCHEN))["items"]
    if len(offers) != 1:
        raise DeviceError(f"expected one offer for the kitchen, got {offers} (round: {ran})")
    day.assignment_id = offers[0]["id"]
    await api.post(f"/v1/assignments/{day.assignment_id}/accept", KITCHEN)
    clock.at = ist(eve, 22)
    await api.job("assignment_acceptance_timeout", clock.at)
    await api.sweep(clock.at)
    say("  21:30 kitchen round: Lata accepted the offer")


async def night_routes(api: Api, day: Day, clock: Clock) -> None:
    """T-1 23:00 to 23:15: grace expiry, the procurement refresh, the van (registered by the hub
    manager, with Arjun as its default rider) and the route solve."""
    eve = day.t - timedelta(days=1)
    clock.at = ist(eve, 23)
    await api.job("payment_grace_expiry", clock.at)
    clock.at = ist(eve, 23, 5)
    await api.job("refresh_procurement_plan", clock.at)
    arjun = (await api.get("/v1/me", RIDER))["id"]
    await api.post(
        f"{HUB}/vehicles",
        MANAGER,
        json={
            "label": "EV-1",
            "kind": "cargo_3w",
            "capacity_kg": 1000,
            "capacity_crates": 60,
            "default_rider_user_id": arjun,
        },
    )
    clock.at = ist(eve, 23, 15)
    solved = await api.job("solve_routes", clock.at)
    await api.sweep(clock.at)
    if solved["failed"]:
        raise DeviceError(f"solve_routes failed: {solved}")
    routes = (
        await api.get(f"{HUB}/routes", MANAGER, params={"date": day.t.isoformat(), "kind": "box"})
    )["items"]
    day.box_routes = [r["id"] for r in routes]
    say(f"  23:15 solve_routes: {solved['published']} plan published, {len(routes)} route(s)")


async def morning(api: Api, day: Day, clock: Clock) -> None:
    """T 04:00 the packer receives the goods; 05:00 packing tasks; the kitchen cooks."""
    t = day.t
    clock.at = ist(t, 4)
    for po in day.purchase_orders:
        lines = [
            {
                "product_id": x["product_id"],
                "received_qty": x["qty"],
                "accepted_qty": x["qty"],
                "grade": "A",
            }
            for x in po["lines"]
        ]
        await api.post(
            f"{HUB}/goods-receipts", PACKER, json={"purchase_order_id": po["id"], "lines": lines}
        )
    say(f"  T 04:00 goods received for {len(day.purchase_orders)} purchase orders")

    clock.at = ist(t, 5)
    built = await api.job("build_packing_tasks", clock.at)
    await api.sweep(clock.at)
    say(f"  05:00 packing: {built['created']} tasks built")

    if not day.assignment_id:
        return
    clock.at = ist(t, 6)
    await api.post(f"/v1/assignments/{day.assignment_id}/start", KITCHEN, json={})
    clock.at = ist(t, 9, 30)
    await api.job("refund_unassigned_tiffins", clock.at)
    arjun = (await api.get("/v1/me", RIDER))["id"]
    await api.post(
        f"{HUB}/vehicles",
        MANAGER,
        json={"label": "TW-1", "kind": "two_wheeler", "default_rider_user_id": arjun},
    )
    clock.at = ist(t, 9, 35)
    solved = await api.job("solve_tiffin_routes", clock.at)
    await api.sweep(clock.at)
    if solved["failed"]:
        raise DeviceError(f"solve_tiffin_routes failed: {solved}")
    clock.at = ist(t, 10, 30)
    checklist = dict.fromkeys(
        ("handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"), True
    )
    await api.post(
        f"/v1/assignments/{day.assignment_id}/ready", KITCHEN, json={"checklist": checklist}
    )
    await api.sweep(clock.at)
    routes = (
        await api.get(f"{HUB}/routes", MANAGER, params={"date": t.isoformat(), "kind": "tiffin"})
    )["items"]
    day.tiffin_routes = [r["id"] for r in routes]
    say(f"  09:35 tiffin solve: {len(routes)} lunch route(s); 10:30 Lata marked the tiffins ready")


async def give_routes_to_the_rider(api: Api, day: Day) -> None:
    """Every route of the day names Arjun as its rider (the van's default usually did it)."""
    arjun = (await api.get("/v1/me", RIDER))["id"]
    for route_id in [*day.box_routes, *day.tiffin_routes]:
        route = await api.get(f"/v1/routes/{route_id}", MANAGER)
        if route["rider"] is None or route["rider"]["user_id"] != arjun:
            await api.call(
                "PATCH",
                f"/v1/routes/{route_id}",
                MANAGER,
                etag=route["version"],
                json={"rider_user_id": arjun},
            )
            day.notes.append(f"route {route_id[:8]} given to the rider by the hub manager")


async def build_day(config: DeviceConfig, api: Api, day: Day) -> None:
    clock = Clock()
    t = day.t
    with clock.installed():
        say(f"building delivery day T = {t} ({t:%A}) on the real clock")
        await set_delivery_day(api, day)
        clock.at = ist(t - timedelta(days=4), 10)
        count = await simulated_households(config, day)
        await subscribe_seed_customers(api, day)
        await api.sweep(clock.at)
        say(f"  {count} simulated households and {len(SEED_SUBSCRIBERS)} seed customers subscribed")

        clock.at = ist(t - timedelta(days=3), 18)
        generated = await api.job("generate_weekly_plans", clock.at)
        await api.sweep(clock.at)
        if generated["delivery_date"] != t.isoformat():
            raise DeviceError(f"generate_weekly_plans planned {generated['delivery_date']}")
        clock.at = ist(t - timedelta(days=3), 18, 30)
        await api.job("send_box_ready_notices", clock.at)
        await api.sweep(clock.at)
        say(f"  T-3 18:00 {generated['created']} boxes drafted; 18:30 box-ready notices sent")

        if config.tiffin:
            await kitchen_setup(config, api, day, clock)

        clock.at = ist(t - timedelta(days=2), 10)
        await customers_approve_and_pay(api, day)
        if config.tiffin:
            await book_tiffin(api, day)
        await api.sweep(clock.at)
        say(f"  T-2 10:00 {len(day.homes)} households approved (or auto-approve) and funded")

        api.forget()
        await evening_before(api, day, clock)
        if config.tiffin:
            await tiffin_round(api, day, clock)
        await night_routes(api, day, clock)
        api.forget()
        await morning(api, day, clock)
        await give_routes_to_the_rider(api, day)
    await outbox.drain()


# --- the summary -----------------------------------------------------------------------------


async def summarise(api: Api, day: Day) -> list[str]:
    """What the phone can expect, read back through the API as the people who see it. Also
    writes `out/codes.txt`."""
    t = day.t
    api.forget()
    tasks = (
        await api.get(f"{HUB}/packing-tasks", MANAGER, params={"date": t.isoformat(), "limit": 200})
    )["items"]
    queued = sum(1 for x in tasks if x["status"] == "queued")
    mine = await api.get("/v1/riders/me/routes", RIDER, params={"date": t.isoformat()})
    routes = [await api.get(f"/v1/routes/{r['id']}", RIDER) for r in mine["items"]]
    captain = await api.get("/v1/captains/me/today", CAPTAIN, params={"date": t.isoformat()})

    names = {p.phone: p.name for p in PEOPLE}
    lines = [f"Delivery day T = {t} ({t:%A}), on the real clock (good until midnight IST)", ""]
    lines.append("Roles (sign in on the phone with these Firebase test numbers):")
    for label, phone in (
        ("customer", CUSTOMER),
        ("captain", CAPTAIN),
        ("captain 2", CAPTAIN_2),
        ("packer", PACKER),
        ("rider", RIDER),
        ("kitchen", KITCHEN),
        ("hub manager", MANAGER),
    ):
        lines.append(f"  {label:<12}{pretty(phone)}  {names[phone]}")
    lines.append("")
    captains = {str(society): (phone, name) for phone, society, name in CAPTAINS}
    for route in routes:
        stops = route["stops"]
        lines.append(
            f"Rider route {route['id']}  {route['kind']}, {route['status']}, {len(stops)} stops"
        )
        for number, stop in enumerate(stops, start=1):
            if stop["kind"] == "kitchen":
                who = "kitchen pickup: scan the GB1A: code"
                where = (stop.get("kitchen") or {}).get("display_name", "kitchen")
            else:
                society = stop["society"]
                where = society["name"]
                cap = captains.get(society["id"])
                who = (
                    f"captain {cap[1]} ({pretty(cap[0])}) takes the handover"
                    if cap
                    else "no captain: the rider delivers door to door (photo or code)"
                )
            lines.append(f"    {number}. {where}: {who}")
    if len(routes) > 1:
        lines.append("  (one route runs at a time: finish the box route before the lunch run)")
    if not routes:
        lines.append("Rider route: NONE (see the notes)")
    lines.append(f"Captain {pretty(CAPTAIN)} sees {len(captain.get('flats', []))} flats today")
    lines.append(f"Packing: {len(tasks)} tasks ({queued} queued), one bag code each")

    codes: list[tuple[str, str]] = []
    by_order = {x["order_id"]: x for x in tasks}
    for home in day.homes:
        if home.phone == CUSTOMER and home.order_id in by_order:
            task = by_order[home.order_id]
            lines.append(f"Customer order {home.order_id}  (her bag: {task['bag_qr']})")
            codes.append((f"Priya's bag, {task['flat_label']}", task["bag_qr"]))
    # Priya's bag, then one bag per society (so every stop of the route has a code to scan).
    societies_seen: set[str] = set()
    for task in tasks:
        name = task["society"]["name"]
        if name not in societies_seen:
            societies_seen.add(name)
            if task["bag_qr"] not in {code for _, code in codes}:
                codes.append((f"{name}, {task['flat_label']}", task["bag_qr"]))
    shown = {code for _, code in codes}
    for task in tasks:
        if task["bag_qr"] not in shown:
            codes.append((f"{task['society']['name']}, {task['flat_label']}", task["bag_qr"]))
    pickup = None
    if day.assignment_id:
        offers = (await api.get(f"/v1/assignments?date={t}", KITCHEN))["items"]
        pickup = next((o.get("pickup_qr") for o in offers if o["id"] == day.assignment_id), None)
        if pickup:
            codes.insert(
                0, ("Lata's kitchen pickup (the rider scans it at the kitchen stop)", pickup)
            )
    lines.append("")
    lines.append("Example bag codes (QR payloads: the packer's and the doors' scans):")
    lines += [f"  {code}   {label}" for label, code in codes if code.startswith("GB1:")][:6]
    lines.append(f"Kitchen pickup code: {pickup or 'none (no tiffin kitchen stop)'}")
    lines += [f"note: {note}" for note in day.notes]

    OUT.mkdir(exist_ok=True)
    with (OUT / "codes.txt").open("w") as handle:
        handle.write(f"# Delivery day {t}: QR payloads, the line after each # comment\n")
        for label, code in codes:
            handle.write(f"# {label}\n{code}\n")
    lines.append(f"All {len(codes)} codes, one per line: {OUT / 'codes.txt'}")
    return lines


def port_of(url: str) -> int:
    return make_url(url.replace("http://", "postgresql://")).port or 80


def port_open(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex((host, port)) == 0


# --- which server is which --------------------------------------------------------------------

SERVER_FILE = OUT / "server.json"


def ensure_port_free(config: DeviceConfig) -> None:
    """Before the build: something already on the port (`just dev`, `just dev-real`) would keep
    uvicorn from binding, and the phone, the ticker and `pack` would then talk to that API and its
    database instead of gharbasket_device."""
    if port_open(config.client_host, config.port):
        raise SystemExit(
            f"Port {config.port} is in use (is `just dev` or `just dev-real` running?). "
            "Stop it, or set DEVICE_PORT to a free port."
        )


def mark_server(config: DeviceConfig) -> None:
    """Written once this process serves the device day, so `pack` knows the API is ours."""
    OUT.mkdir(parents=True, exist_ok=True)
    SERVER_FILE.write_text(json.dumps({"port": config.port, "pid": os.getpid()}))


def ensure_device_server(config: DeviceConfig) -> None:
    """`pack` writes through the API on the port: only when it is this tool's server."""
    try:
        mark = json.loads(SERVER_FILE.read_text())
        os.kill(int(mark["pid"]), 0)  # the process is alive (signal 0 sends nothing)
        ours = mark["port"] == config.port and port_open(config.client_host, config.port)
    except (OSError, ValueError, KeyError, TypeError):
        ours = False
    if not ours:
        raise SystemExit(
            f"No device-day server on port {config.port}: start `just device-day` first. "
            "`pack` never writes to another API (it could be `just dev` on gharbasket)."
        )


def connection_lines(config: DeviceConfig) -> list[str]:
    api, files = config.port, port_of(config.s3_url)
    lines = [
        "",
        "Phone (USB debugging on):",
        f"  adb reverse tcp:{api} tcp:{api}      # the API, http://localhost:{api}",
        f"  adb reverse tcp:{files} tcp:{files}      # photo uploads (SeaweedFS)",
        "",
        f"API        http://{config.host}:{api}   (docs: /v1/docs)",
        f"Firebase   project {config.firebase_project}, plus dev sign-in with a phone number",
        f"JOBS token {config.jobs_token}",
        "           Authorization: Bearer <token> on POST /v1/internal/jobs/<name>",
        "Pack the queue from another terminal: just device-day pack [--leave 3]",
        "Stop with Ctrl-C; the next `just device-day` rebuilds the database.",
    ]
    if not config.loopback:
        lines[1:1] = [
            "!" * 78,
            f"WARNING: the API listens on {config.host}: anyone on this Wi-Fi can dev-sign-in",
            "as any seed person, the admin included, with only a phone number. Use it on a",
            "network you trust, and stop it when you are done.",
            "!" * 78,
        ]
    if not port_open("localhost", files):
        lines.insert(
            1, f"WARNING: nothing listens on port {files}: `just up-storage` (photos will fail)"
        )
    return lines


# --- serving ---------------------------------------------------------------------------------


async def ticker(config: DeviceConfig) -> None:
    """The every-minute crons of a delivery morning, so the phone sees the day move: GPS pings
    to Postgres, the outbox swept, deliveries sent."""
    headers = {"Authorization": f"Bearer {config.jobs_token}"}
    base = f"http://{config.client_host}:{config.port}"
    failing: set[str] = set()  # said once, until the job works again
    async with httpx2.AsyncClient(base_url=base, timeout=30.0) as http:
        while True:
            await asyncio.sleep(60)
            for name in ("persist_gps_pings", "outbox_sweep", "send_deliveries"):
                try:
                    answer = await http.post(f"/v1/internal/jobs/{name}", json={}, headers=headers)
                    problem = None if answer.status_code == 200 else f"HTTP {answer.status_code}"
                except httpx2.HTTPError as error:
                    problem = type(error).__name__
                if problem and name not in failing:
                    say(f"ticker: {name} failed ({problem}); the phone's day may stop moving")
                    failing.add(name)
                elif not problem:
                    failing.discard(name)


async def serve(config: DeviceConfig) -> None:
    app = create_app(config.settings())
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=config.host,
            port=config.port,
            log_level="info",
            access_log=True,
            loop="asyncio",
        )
    )
    background = asyncio.create_task(ticker(config))
    mark_server(config)
    try:
        await server.serve()
    finally:
        background.cancel()
        SERVER_FILE.unlink(missing_ok=True)


async def prepare(config: DeviceConfig) -> list[str]:
    """Rebuild the database, walk the day and return the summary lines."""
    await build_database(config)
    day = Day(datetime.now(IST).date())
    originals = (identity_router.EXCHANGE_PER_IP, identity_router.EXCHANGE_PER_PHONE)
    # Many sign-ins in a few minutes while building: the limiter's two constants, for this phase.
    identity_router.EXCHANGE_PER_IP = identity_router.EXCHANGE_PER_PHONE = (100_000, 600)
    try:
        async with AsyncExitStack() as stack:
            api = Api(config)
            await api.start(stack)
            await build_day(config, api, day)
            lines = await summarise(api, day)
    finally:
        identity_router.EXCHANGE_PER_IP, identity_router.EXCHANGE_PER_PHONE = originals
    await flush_valkey(config.valkey_url)  # the build's sign-ins must not count against the phone
    return lines


# --- the packing helper ----------------------------------------------------------------------


async def pack(config: DeviceConfig, leave: int) -> None:
    """Pack the queue through the running server as the packer (`POST …/packing-tasks/next`, pick
    every line, complete), leaving `leave` bags for the phone."""
    ensure_device_server(config)
    base = f"http://{config.client_host}:{config.port}"
    async with httpx2.AsyncClient(base_url=base, timeout=60.0) as http:
        signed = await http.post(
            "/v1/auth/exchange",
            json={"dev_phone": PACKER, "device": {"platform": "web", "push_token": None}},
        )
        signed.raise_for_status()
        auth = {"Authorization": f"Bearer {signed.json()['access_token']}"}

        def keyed() -> dict[str, str]:
            return {**auth, "Idempotency-Key": str(uuid.uuid4())}

        today = datetime.now(IST).date().isoformat()
        listed = await http.get(
            f"{HUB}/packing-tasks", params={"date": today, "limit": 200}, headers=auth
        )
        listed.raise_for_status()
        queued = sum(1 for x in listed.json()["items"] if x["status"] == "queued")
        done = 0
        for _ in range(max(queued - leave, 0)):
            claimed = await http.post(f"{HUB}/packing-tasks/next", headers=keyed())
            if claimed.status_code != 200:
                break
            task = claimed.json()
            for line in task["lines"]:
                # The packer's own open bag comes back first; lines done on the phone (picked or
                # marked short) are left as they are.
                if line.get("done"):
                    continue
                picked = await http.post(
                    f"/v1/packing-tasks/{task['id']}/pick",
                    json={"product_id": line["product_id"], "picked_qty": line["qty"]},
                    headers=keyed(),
                )
                picked.raise_for_status()
            finished = await http.post(
                f"/v1/packing-tasks/{task['id']}/complete", json={}, headers=keyed()
            )
            finished.raise_for_status()
            done += 1
        say(f"packed {done} of {queued} queued bags; {queued - done} left for the phone")


# --- command line ----------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="A delivery day for today on a local backend, for testing the app on a phone."
    )
    parser.add_argument(
        "command", nargs="?", default="run", choices=["run", "prepare", "serve", "pack"]
    )
    parser.add_argument("--leave", type=int, default=0, help="pack: bags to leave in the queue")
    args = parser.parse_args(argv)
    # No OpenTelemetry from this process or the ones it starts (settings() also leaves the OTLP
    # endpoint out): a phone test day never shows up in staging's traces.
    os.environ["OTEL_SDK_DISABLED"] = "true"
    config = DeviceConfig.from_env()
    config.check()  # first, before anything is dropped, flushed or written

    if args.command == "pack":
        asyncio.run(pack(config, args.leave))
        return 0
    if args.command in ("run", "serve"):
        ensure_port_free(config)  # before the database is rebuilt, not after
    lines: list[str] = []
    if args.command != "serve":
        lines = asyncio.run(prepare(config))
    if args.command == "prepare":
        for line in lines:
            say(line)
        return 0
    say("")
    say("=" * 78)
    for line in [*lines, *connection_lines(config)]:
        say(line)
    say("=" * 78)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
