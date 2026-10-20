"""What the simulated day runs on: its settings, the app over HTTP with real commits, the clock,
and stand-ins for the only outside services the day reaches (Google's sign-in keys and FCM).

Everything else is real: a fresh Postgres database, Valkey, the after-commit outbox relay, the
job endpoints and the ML fallbacks (or the ML service with `E2E_ML=1`).
"""

import json
import os
import time
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import jwt
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from pydantic import SecretStr
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.engine import make_url

from gharbasket.main import create_app
from gharbasket.modules.identity.firebase import CERTS_URL, FirebaseTokenVerifier
from gharbasket.modules.notifications.fcm import FcmClient
from gharbasket.platform import outbox
from gharbasket.platform.db import create_engine, is_local_database
from gharbasket.platform.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "backend" / "api"
FIREBASE_PROJECT = "gharbasket-e2e"
JOBS_TOKEN = "e2e-jobs-token"
FCM_TOKEN_URI = "https://oauth2.e2e.test/token"
FCM_SEND = "https://fcm.googleapis.com/v1/projects/gharbasket-e2e/messages:send"


def ist(day: date, hour: int, minute: int = 0) -> datetime:
    """An IST wall-clock time on `day`, as UTC (what `scheduled_for` and the clock take)."""
    return datetime.combine(day, clock_time(hour, minute), tzinfo=IST).astimezone(UTC)


@dataclass(frozen=True)
class Config:
    """The run's settings, from the environment (`just e2e-day` sets none of them)."""

    database_url: str
    valkey_url: str
    keep_db: bool
    ml_url: str | None
    ml_token: str | None
    sim_seed: int
    sim_weeks: int
    sim_adoption: float
    first_monday: date

    @classmethod
    def from_env(cls) -> "Config":
        ml_on = os.environ.get("E2E_ML", "") == "1"
        return cls(
            database_url=os.environ.get(
                "E2E_DATABASE_URL",
                "postgresql+asyncpg://gharbasket:gharbasket@localhost:5432/gharbasket_e2e",
            ),
            valkey_url=os.environ.get("E2E_VALKEY_URL", "redis://localhost:6380/14"),
            keep_db=os.environ.get("E2E_KEEP_DB", "") == "1",
            ml_url=os.environ.get("E2E_ML_URL", "http://localhost:8100") if ml_on else None,
            ml_token=os.environ.get("E2E_ML_TOKEN", os.environ.get("ML_SERVICE_TOKEN")),
            sim_seed=int(os.environ.get("E2E_SIM_SEED", "7")),
            sim_weeks=int(os.environ.get("E2E_SIM_WEEKS", "2")),
            sim_adoption=float(os.environ.get("E2E_SIM_ADOPTION", "0.03")),
            # The simulator's default start: the seed calendar and prices cover these weeks.
            first_monday=date.fromisoformat(os.environ.get("E2E_START", "2026-10-05")),
        )

    def check(self) -> None:
        """The suite drops and recreates its database: never one it does not own."""
        url = make_url(self.database_url)
        if not is_local_database(self.database_url):
            raise SystemExit("E2E_DATABASE_URL must be the local Docker database")
        if "e2e" not in (url.database or ""):
            raise SystemExit("E2E_DATABASE_URL's database name must contain 'e2e' (it is dropped)")

    @property
    def planner(self) -> str:
        """The simulator's planner: CP-SAT in process when the ML service is up, else the rules."""
        return "cpsat" if self.ml_url else "rules"

    @property
    def delivery_day(self) -> date:
        """The day driven through the jobs: the Tuesday (the seed's delivery day) of the week
        after the simulated ones."""
        monday = self.first_monday + timedelta(weeks=self.sim_weeks)
        return monday + timedelta(days=1)

    def sub_env(self) -> dict[str, str]:
        """The environment for `just seed` and `just sim` run as their own processes."""
        return {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "DATABASE_URL_DIRECT": self.database_url,
            "LOG_LEVEL": "WARNING",
        }

    def settings(self, fcm_account: dict[str, Any]) -> Settings:
        return Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            app_env="test",
            log_level="WARNING",
            database_url=SecretStr(self.database_url),
            valkey_url=SecretStr(self.valkey_url),
            jobs_token=SecretStr(JOBS_TOKEN),
            firebase_project_id=FIREBASE_PROJECT,
            fcm_service_account=SecretStr(json.dumps(fcm_account)),
            ml_service_url=self.ml_url,
            ml_service_token=SecretStr(self.ml_token) if self.ml_token else None,
        )


# --- databases, jobs -----------------------------------------------------------------------------


async def recreate_database(url: str, *, template: str | None = None, create: bool = True) -> None:
    """Drop the database `url` names (connections are cut) and create it again, empty or as a copy
    of the database `template` (which must have no open connection). Only a local database."""
    if not is_local_database(url):
        raise SystemExit("recreate_database: the local Docker database only")
    target = make_url(url)
    admin = target.set(database="postgres").render_as_string(hide_password=False)
    engine = create_engine(admin, pooled=False)
    try:
        async with engine.connect() as connection:
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
            await connection.execute(
                text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)')
            )
            if create:
                copy = f' TEMPLATE "{template}"' if template else ""
                await connection.execute(text(f'CREATE DATABASE "{target.database}"{copy}'))
    finally:
        await engine.dispose()


async def flush_valkey(url: str) -> None:
    """Empty the Valkey database `url` names (rate limits, locks, caches)."""
    valkey = Redis.from_url(url)
    try:
        await valkey.flushdb()
    finally:
        await valkey.aclose()


async def post_job(http: httpx2.AsyncClient, name: str, at: datetime) -> tuple[int, dict[str, Any]]:
    """Trigger a job as the external cron does (the operator token and `scheduled_for`): the HTTP
    status and the run (`status` ok, failed or skipped)."""
    answer = await http.post(
        f"/v1/internal/jobs/{name}",
        json={"scheduled_for": at.isoformat()},
        headers={"Authorization": f"Bearer {JOBS_TOKEN}"},
    )
    return answer.status_code, answer.json()


async def sweep_outbox(http: httpx2.AsyncClient, at: datetime) -> int:
    """The after-commit relays finish, then `outbox_sweep` runs until nothing is due, as the
    every-few-minutes cron would (consumers that publish need a second pass). Returns how many
    messages the sweeps sent."""
    await outbox.drain()
    sent = 0
    for _ in range(10):
        status, body = await post_job(http, "outbox_sweep", at)
        assert status == 200 and body["status"] == "ok", body
        summary = body["summary"]
        sent += summary["sent"]
        if summary["sent"] == 0 and summary["failed"] == 0:
            break
    return sent


# --- Google's sign-in keys and FCM ---------------------------------------------------------------


class FakeGoogle:
    """Firebase's signing keys (a real RSA key and certificate) and the ID tokens they sign, so
    `POST /v1/auth/exchange` verifies them exactly as it does Google's."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    def _certificate(self) -> str:
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "securetoken.e2e")])
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .sign(self.key, hashes.SHA256())
        )
        return cert.public_bytes(serialization.Encoding.PEM).decode()

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == CERTS_URL
        return httpx2.Response(
            200, json={"kid-1": self._certificate()}, headers={"Cache-Control": "max-age=60"}
        )

    def verifier(self) -> FirebaseTokenVerifier:
        client = httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))
        return FirebaseTokenVerifier(FIREBASE_PROJECT, client)

    def id_token(self, phone: str) -> str:
        now = int(time.time())
        claims = {
            "iss": f"https://securetoken.google.com/{FIREBASE_PROJECT}",
            "aud": FIREBASE_PROJECT,
            "sub": f"uid{phone}",
            "iat": now - 5,
            "exp": now + 3600,
            "auth_time": now - 5,
            "phone_number": phone,
            "firebase": {"sign_in_provider": "phone"},
        }
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "kid-1"})


def fcm_account() -> dict[str, Any]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return {
        "type": "service_account",
        "project_id": "gharbasket-e2e",
        "private_key": pem,
        "client_email": "push@gharbasket-e2e.iam.gserviceaccount.com",
        "token_uri": FCM_TOKEN_URI,
    }


@dataclass
class Outside:
    """The app's outbound HTTP client: Google's token endpoint and FCM are answered here (every
    push is recorded), anything else (the ML service with `E2E_ML=1`) really goes out."""

    pushes: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    passed: list[str] = field(default_factory=list[str])

    def __post_init__(self) -> None:
        self._real = httpx2.AsyncHTTPTransport()

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == FCM_TOKEN_URI:
            return httpx2.Response(200, json={"access_token": "ya29.e2e", "expires_in": 3599})
        if url == FCM_SEND:
            message = json.loads(request.content)["message"]
            self.pushes.append(message)
            return httpx2.Response(
                200, json={"name": f"projects/gharbasket-e2e/messages/{uuid.uuid4()}"}
            )
        self.passed.append(url)
        return await self._real.handle_async_request(request)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self.handle), timeout=10.0)


# --- the app ---------------------------------------------------------------------------------


SUMMARY: list[str] = []  # lines for the run's summary (the simulator's report)


@dataclass
class JobLog:
    at: datetime
    name: str
    status: str
    summary: dict[str, Any] | None


@dataclass
class App:
    """The API as a phone or cron-job.org reaches it: HTTP into the real app (its own sessions,
    its own commits, its after-commit relay), started and stopped with its lifespan."""

    config: Config
    google: FakeGoogle = field(default_factory=FakeGoogle)
    outside: Outside = field(default_factory=Outside)
    jobs: list[JobLog] = field(default_factory=list[JobLog])
    devices: dict[str, str] = field(default_factory=dict[str, str])  # phone -> push token
    refresh_tokens: dict[str, str] = field(default_factory=dict[str, str])  # phone -> token

    async def start(self) -> None:
        FcmClient._tokens.clear()  # pyright: ignore[reportPrivateUsage]
        self.app: FastAPI = create_app(self.config.settings(fcm_account()))
        self._stack = AsyncExitStack()
        await self._stack.enter_async_context(self.app.router.lifespan_context(self.app))
        # After startup: the lifespan's own outbound client and Firebase verifier are replaced.
        await self.app.state.http.aclose()
        self.app.state.http = self.outside.client()
        self.app.state.firebase_verifier = self.google.verifier()
        self.http = await self._stack.enter_async_context(
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=self.app), base_url="https://e2e")
        )

    async def stop(self) -> None:
        await self.app.state.http.aclose()
        await self._stack.aclose()

    async def trigger(self, name: str, at: datetime) -> dict[str, Any]:
        """Trigger a job as the external cron does (the operator token and `scheduled_for`);
        returns the run (`status` ok, failed or skipped)."""
        code, body = await post_job(self.http, name, at)
        status = body.get("status", body.get("code", "?"))
        self.jobs.append(JobLog(at, name, status, body.get("summary") or body.get("run_id")))
        assert code == 200, f"{name} at {at}: {body}"
        return body

    async def job(self, name: str, at: datetime) -> dict[str, Any]:
        """Run a job that must succeed; returns its summary."""
        body = await self.trigger(name, at)
        assert body["status"] == "ok", body
        return body["summary"]

    async def sweep(self, at: datetime) -> None:
        """The after-commit relays finish, then `outbox_sweep` runs until nothing is due, as the
        every-few-minutes cron would (consumers that publish need a second pass)."""
        await outbox.drain()
        for _ in range(10):
            summary = await self.job("outbox_sweep", at)
            if summary["sent"] == 0 and summary["failed"] == 0:
                return

    async def sign_in(self, phone: str) -> dict[str, str]:
        """Sign in the way the app does (a Firebase ID token for the phone), on an Android device
        with a push token; returns the bearer header."""
        token = self.devices.setdefault(phone, f"e2e-device-{phone[1:]}")
        answer = await self.http.post(
            "/v1/auth/exchange",
            json={
                "firebase_id_token": self.google.id_token(phone),
                "device": {"platform": "android", "push_token": token},
            },
        )
        assert answer.status_code == 200, answer.text
        self.refresh_tokens[phone] = answer.json()["refresh_token"]
        return {"Authorization": f"Bearer {answer.json()['access_token']}"}

    async def refresh(self, phone: str) -> dict[str, str]:
        """The app coming back after a while: its access token (15 minutes) has expired, so it
        trades the refresh token for new ones."""
        answer = await self.http.post(
            "/v1/auth/refresh", json={"refresh_token": self.refresh_tokens[phone]}
        )
        assert answer.status_code == 200, answer.text
        self.refresh_tokens[phone] = answer.json()["refresh_token"]
        return {"Authorization": f"Bearer {answer.json()['access_token']}"}


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}
