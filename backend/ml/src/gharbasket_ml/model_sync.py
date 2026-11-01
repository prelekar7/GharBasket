"""Following the R2 model registry (ADR-0025, ADR-0030, M10 §3.7): the served models' pointers
and files, synced into a local folder that the ranker and `/models` read like `MODELS_DIR`.

The registry is what `backend/training` writes: `models/<name>/<version>/...` in
`S3_BUCKET_MLFLOW` (`gb-mlflow`) and the pointer `models/<name>/latest.json`
(`{"version": ..., "promoted_at": ..., "why": ..., "previous": ...}`), moved only by
`just promote`. The cache folder has the same layout without the `models/` prefix.

Every `MODELS_RELOAD_S`, in a background thread (never on a request, never before `/health`):
- read each served model's pointer; nothing promoted (404) leaves the cache as it is;
- a version already in the cache is not downloaded again (an unchanged pointer, a rollback);
- a new version is streamed to disk into `<name>/.tmp-<version>-*`, renamed to `<name>/<version>/`
  once every file is there, and only then is `<name>/latest.json` replaced (an atomic rename), so a
  half-written version is never served: the ranker only follows the pointer and refuses versions
  starting with a dot;
- the current and the previous version are kept, older ones removed.

Read only: GET requests signed with AWS Signature V4 over `httpx2` (already a dependency; boto3
would cost about 30 MB of memory on a 512 MB instance). Every request has a timeout, every file a
size cap and the whole sync a deadline. Any registry error is logged and recorded for
`/health/ai`; the models already synced keep serving, or the rule fallback answers.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import quote, urlsplit

import httpx2

from gharbasket_ml import ranker
from gharbasket_ml.settings import Settings

log = logging.getLogger(__name__)

POINTER = "latest.json"
PREFIX = "models/"
POINTER_MAX_BYTES = 64 * 1024
ARTEFACT_MAX_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True)
class Artefacts:
    required: tuple[str, ...]
    optional: tuple[str, ...] = ()


# The models this service serves from files, and what it needs of a version. The pantry priors
# and the forecasts are pushed to the API by the training jobs, not served from here.
SERVED: Mapping[str, Artefacts] = {
    ranker.MODEL_NAME: Artefacts(required=("model.txt",), optional=("card.json",)),
}


class RegistryError(Exception):
    """The registry answered something we cannot use (the message names keys, never secrets)."""


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sign_v4(
    method: str,
    url: str,
    headers: Mapping[str, str],
    *,
    access_key: str,
    secret_key: str,
    region: str,
    now: datetime,
    service: str = "s3",
    payload_hash: str = EMPTY_SHA256,
) -> dict[str, str]:
    """`headers` plus `x-amz-date`, `x-amz-content-sha256` and the `Authorization` of AWS
    Signature Version 4 for a request without a query string (the path must be URI-encoded)."""
    parts = urlsplit(url)
    if parts.query:
        raise ValueError("sign_v4 signs requests without a query string only")
    amz_date = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    day = amz_date[:8]
    signed = {name.lower(): " ".join(value.split()) for name, value in headers.items()}
    signed |= {
        "host": parts.netloc.lower(),
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    names = sorted(signed)
    signed_headers = ";".join(names)
    canonical = "\n".join(
        (
            method,
            parts.path or "/",
            "",
            "".join(f"{name}:{signed[name]}\n" for name in names),
            signed_headers,
            payload_hash,
        )
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest())
    )
    key = _hmac(f"AWS4{secret_key}".encode(), day)
    for part in (region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    return dict(headers) | {
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
        "Authorization": (
            f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }


class Reader(Protocol):
    def fetch(self, key: str, dest: Path, *, max_bytes: int, deadline: float) -> bool:
        """Stream the object `key` into `dest`: False when it does not exist; RegistryError
        (or an httpx2 error) otherwise. `dest` may hold a partial file after an error."""
        ...

    def close(self) -> None: ...


class R2Reader:
    """GET-only access to one bucket of an S3-API store (Cloudflare R2, SeaweedFS locally),
    path-style, with a timeout on every request."""

    def __init__(
        self,
        endpoint_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "auto",
        *,
        timeout_s: float = 10.0,
        transport: httpx2.BaseTransport | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._now = now
        self._clock = clock
        self._client = httpx2.Client(
            timeout=httpx2.Timeout(timeout_s, connect=min(timeout_s, 5.0)),
            transport=transport,
            follow_redirects=False,
            trust_env=False,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "R2Reader":
        if not (settings.s3_endpoint_url and settings.s3_access_key and settings.s3_secret_key):
            raise ValueError("set S3_ENDPOINT_URL, S3_ACCESS_KEY and S3_SECRET_KEY")
        return cls(
            settings.s3_endpoint_url,
            settings.s3_bucket_mlflow,
            settings.s3_access_key,
            settings.s3_secret_key.get_secret_value(),
            settings.s3_region,
            timeout_s=settings.models_sync_timeout_s,
        )

    def url(self, key: str) -> str:
        return f"{self._base}/{quote(self._bucket, safe='')}/{quote(key, safe='/-_.~')}"

    def fetch(self, key: str, dest: Path, *, max_bytes: int, deadline: float) -> bool:
        url = self.url(key)
        headers = sign_v4(
            "GET",
            url,
            {},
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            now=self._now(),
        )
        with self._client.stream("GET", url, headers=headers) as answer:
            if answer.status_code == 404:
                if b"NoSuchBucket" in answer.read()[:4096]:
                    raise RegistryError(f"bucket {self._bucket} does not exist")
                return False
            if answer.status_code != 200:
                raise RegistryError(f"GET {key}: HTTP {answer.status_code}")
            size = 0
            with dest.open("wb") as out:
                for chunk in answer.iter_bytes(CHUNK_BYTES):
                    size += len(chunk)
                    if size > max_bytes:
                        raise RegistryError(f"{key} is larger than {max_bytes} bytes")
                    if self._clock() > deadline:
                        raise RegistryError(f"the sync deadline passed while reading {key}")
                    out.write(chunk)
        return True

    def close(self) -> None:
        self._client.close()


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def local_version(folder: Path) -> str | None:
    """The version a cached pointer names, None when there is none or it is unreadable."""
    try:
        version = json.loads((folder / POINTER).read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if isinstance(version, str) and _VERSION.fullmatch(version) else None


def _pointed_version(raw: bytes, key: str) -> str:
    try:
        version = json.loads(raw).get("version")
    except (ValueError, AttributeError) as e:
        raise RegistryError(f"{key} is not a JSON object") from e
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        raise RegistryError(f"{key} names no usable version: {str(version)[:80]!r}")
    return version


class ModelSync:
    """Keeps `cache_dir` in step with the registry's pointers for `models` (see the module)."""

    def __init__(
        self,
        reader: Reader,
        cache_dir: Path,
        *,
        bucket: str,
        models: Mapping[str, Artefacts] = SERVED,
        deadline_s: float = 120.0,
        on_change: Callable[[list[str]], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.reader = reader
        self.cache_dir = cache_dir
        self.bucket = bucket
        self.models = models
        self.deadline_s = deadline_s
        self.on_change = on_change
        self._clock = clock
        self._wallclock = wallclock
        self._lock = threading.Lock()  # one sync at a time
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = None

    def _key(self, name: str, *parts: str) -> str:
        return "/".join((f"{PREFIX}{name}", *parts))

    def sync_once(self) -> list[str]:
        """One pass over the served models; the names whose served version changed. Never
        raises: an error is logged, recorded, and the cache keeps what it had."""
        with self._lock:
            started = self._wallclock()
            self.last_attempt_at = started
            deadline = self._clock() + self.deadline_s
            changed: list[str] = []
            errors: list[str] = []
            for name, artefacts in self.models.items():
                try:
                    if self._sync_model(name, artefacts, deadline):
                        changed.append(name)
                except RegistryError as e:
                    log.warning("model %s not synced from the registry: %s", name, e)
                    errors.append(f"{name}: {e}")
                except Exception as e:  # network, disk: the service keeps what it has
                    log.warning(  # one line, no traceback: an outage repeats every interval
                        "model %s not synced from the registry: %s: %s", name, type(e).__name__, e
                    )
                    errors.append(f"{name}: {type(e).__name__}")
            if changed and self.on_change is not None:
                try:
                    self.on_change(changed)
                except Exception:
                    log.exception("reloading %s after the sync failed", changed)
            if errors:
                self.last_failure_at = started
                self.last_error = "; ".join(errors)
            else:
                self.last_success_at = started
            return changed

    def _sync_model(self, name: str, artefacts: Artefacts, deadline: float) -> bool:
        folder = self.cache_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        self._remove_leftovers(folder)
        key = self._key(name, POINTER)
        incoming = folder / f".{POINTER}.{uuid.uuid4().hex}.part"
        try:
            if not self.reader.fetch(key, incoming, max_bytes=POINTER_MAX_BYTES, deadline=deadline):
                log.info("model %s: nothing promoted in the registry yet", name)
                return False
            version = _pointed_version(incoming.read_bytes(), key)
            current = local_version(folder)
            if version == current and (folder / version).is_dir():
                return False
            if not (folder / version).is_dir():
                self._download(name, version, artefacts, folder, deadline)
            os.replace(incoming, folder / POINTER)
            self._prune(folder, keep={version, current})
            log.info("model %s: serving %s from the registry (was %s)", name, version, current)
            return True
        finally:
            incoming.unlink(missing_ok=True)

    def _download(
        self, name: str, version: str, artefacts: Artefacts, folder: Path, deadline: float
    ) -> None:
        staging = Path(tempfile.mkdtemp(prefix=f".tmp-{version}-", dir=folder))
        try:
            for file in artefacts.required:
                key = self._key(name, version, file)
                if not self.reader.fetch(
                    key, staging / file, max_bytes=ARTEFACT_MAX_BYTES, deadline=deadline
                ):
                    raise RegistryError(f"{key} is missing")
            for file in artefacts.optional:
                key = self._key(name, version, file)
                if not self.reader.fetch(
                    key, staging / file, max_bytes=ARTEFACT_MAX_BYTES, deadline=deadline
                ):
                    (staging / file).unlink(missing_ok=True)
            staging.chmod(0o755)
            staging.rename(folder / version)
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _remove_leftovers(folder: Path) -> None:
        """Downloads and pointers an interrupted sync left behind (never served)."""
        for child in folder.iterdir():
            if child.name.startswith(".tmp-") and child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            elif child.name.startswith(f".{POINTER}.") and child.name.endswith(".part"):
                child.unlink(missing_ok=True)

    @staticmethod
    def _prune(folder: Path, keep: set[str | None]) -> None:
        for child in folder.iterdir():
            if child.is_dir() and child.name not in keep and not child.name.startswith("."):
                shutil.rmtree(child, ignore_errors=True)

    def status(self) -> dict[str, object]:
        """What `/health/ai` shows: the synced versions and the last sync, without secrets."""
        return {
            "enabled": True,
            "bucket": self.bucket,
            "models": {name: local_version(self.cache_dir / name) for name in self.models},
            "last_attempt_at": _iso(self.last_attempt_at),
            "last_success_at": _iso(self.last_success_at),
            "last_failure_at": _iso(self.last_failure_at),
            "last_error": self.last_error,
        }

    async def run_forever(self, interval_s: float) -> None:
        """Sync now, then every `interval_s`, off the event loop; until cancelled."""
        while True:
            try:
                await asyncio.to_thread(self.sync_once)
            except Exception:  # sync_once does not raise; belt and braces for the loop
                log.exception("the model sync failed")
            await asyncio.sleep(interval_s)

    def close(self) -> None:
        self.reader.close()
