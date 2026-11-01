"""Following the R2 model registry (ADR-0025, ADR-0030, M10 §3.7): the served models' pointers
and files, synced into a local folder that the ranker and `/models` read like `MODELS_DIR`.

The registry is what `backend/training` writes: `models/<name>/<version>/...` in
`S3_BUCKET_MLFLOW` (`gb-mlflow`) and the pointer `models/<name>/latest.json`
(`{"version": ..., "promoted_at": ..., "why": ..., "previous": ...}`), moved only by
`just promote`. The cache folder reads like `MODELS_DIR` (`<name>/latest.json`,
`<name>/<version>/model.txt`); each `<name>/<version>` is a symlink into `<name>/.v/`, where the
downloaded copies live with the ETags they were fetched with (`.source.json`).

At startup and every `MODELS_RELOAD_S`, in a background thread (never on a request, never
before `/health`):
- read each served model's pointer; nothing promoted (404) leaves the cache as it is;
- a version already cached is not downloaded again (an unchanged pointer, a rollback) unless the
  registry's copy changed: versions are not immutable (`train_ranker --version` defaults to the
  day, and a rerun overwrites), so the required files' ETags are compared with one HEAD each;
- a new or changed version is streamed to disk into a fresh folder under `.v/`, the
  `<version>` symlink is switched to it once every file is there, and only then is
  `<name>/latest.json` replaced (both atomic renames), so a half-written version is never
  served: the ranker only follows the pointer and refuses versions starting with a dot;
- the current and the previous version are kept, older ones removed.

Read only: GET and HEAD requests signed with AWS Signature V4 over `httpx2` (already a
dependency; boto3 would cost about 30 MB of memory on a 512 MB instance), bodies asked for and
read unencoded. Every request has a timeout; every piece read is checked against the file's size
cap and the sync's deadline. Any registry error is logged and recorded for `/health/ai`; the
models already synced keep serving, or the rule fallback answers. On Render free the cache under
`/tmp` starts empty after every wake, so the fallback answers until the first sync completes.
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
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
STORE = ".v"  # the downloaded versions; `<name>/<version>` is a symlink into it
SOURCE = ".source.json"  # beside a downloaded version's files: the ETags it was fetched with


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


def _host(url: str) -> str:
    """The Host header an HTTP client sends for `url`: lower case, without a default port."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if ":" in host:  # an IPv6 literal
        host = f"[{host}]"
    port = parts.port
    if port is not None and (parts.scheme, port) not in (("http", 80), ("https", 443)):
        host = f"{host}:{port}"
    return host


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
        "host": _host(url),
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


Expired = Callable[[], bool]  # True once the sync must give up (its deadline passed, or a stop)


class Reader(Protocol):
    def fetch(self, key: str, dest: Path, *, max_bytes: int, expired: Expired) -> str | None:
        """Stream the object `key` into `dest` and return its ETag ("" when the store sends
        none); None when it does not exist; RegistryError (or an httpx2 error) otherwise.
        `dest` may hold a partial file after an error."""
        ...

    def etag(self, key: str, *, expired: Expired) -> str | None:
        """The object's ETag without its body (HEAD); None when it does not exist."""
        ...

    def close(self) -> None: ...


class R2Reader:
    """GET/HEAD-only access to one bucket of an S3-API store (Cloudflare R2, SeaweedFS
    locally), path-style, with a timeout on every request."""

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
    ) -> None:
        self._base = endpoint_url.rstrip("/")
        self._bucket = bucket
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._now = now
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

    def _headers(self, method: str, url: str) -> dict[str, str]:
        signed = sign_v4(
            method,
            url,
            {},
            access_key=self._access_key,
            secret_key=self._secret_key,
            region=self._region,
            now=self._now(),
        )
        # Unsigned: the body as stored, never gzip that would decode past the size cap.
        return signed | {"Accept-Encoding": "identity"}

    def _missing(self, answer: httpx2.Response) -> None:
        """A 404: a missing key is an answer, a missing bucket an error (the first 4 KiB of
        the error body tell them apart)."""
        head = b""
        for piece in answer.iter_bytes():
            head += piece
            if len(head) >= 4096:
                break
        if b"NoSuchBucket" in head[:4096]:
            raise RegistryError(f"bucket {self._bucket} does not exist")

    def fetch(self, key: str, dest: Path, *, max_bytes: int, expired: Expired) -> str | None:
        if expired():
            raise RegistryError(f"the sync gave up before reading {key}")
        url = self.url(key)
        with self._client.stream("GET", url, headers=self._headers("GET", url)) as answer:
            if answer.status_code == 404:
                self._missing(answer)
                return None
            if answer.status_code != 200:
                raise RegistryError(f"GET {key}: HTTP {answer.status_code}")
            encoding = answer.headers.get("content-encoding", "identity").lower()
            if encoding not in ("", "identity"):
                raise RegistryError(f"{key} came {encoding}-encoded; only identity is read")
            size = 0
            with dest.open("wb") as out:
                # Each piece as it arrives: the cap and the deadline are checked on every one,
                # so a trickling store cannot hold a sync past its deadline (plus one read).
                for piece in answer.iter_bytes():
                    size += len(piece)
                    if size > max_bytes:
                        raise RegistryError(f"{key} is larger than {max_bytes} bytes")
                    if expired():
                        raise RegistryError(f"the sync gave up while reading {key}")
                    out.write(piece)
            return answer.headers.get("etag", "")

    def etag(self, key: str, *, expired: Expired) -> str | None:
        if expired():
            raise RegistryError(f"the sync gave up before checking {key}")
        url = self.url(key)
        answer = self._client.head(url, headers=self._headers("HEAD", url))
        if answer.status_code == 404:
            return None
        if answer.status_code != 200:
            raise RegistryError(f"HEAD {key}: HTTP {answer.status_code}")
        return answer.headers.get("etag", "")

    def close(self) -> None:
        self._client.close()


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _usable(version: object) -> bool:
    """A version that can name a folder beside the pointer: no path, no dot first, and not the
    pointer's own name or any other `.json` file."""
    return (
        isinstance(version, str)
        and _VERSION.fullmatch(version) is not None
        and not version.lower().endswith(".json")
    )


def local_version(folder: Path) -> str | None:
    """The version a cached pointer names, None when there is none or it is unreadable."""
    try:
        version = json.loads((folder / POINTER).read_text()).get("version")
    except (OSError, ValueError, AttributeError):
        return None
    return version if _usable(version) else None


def _pointed_version(raw: bytes, key: str) -> str:
    try:
        version = json.loads(raw).get("version")
    except (ValueError, AttributeError) as e:
        raise RegistryError(f"{key} is not a JSON object") from e
    if not _usable(version):
        raise RegistryError(f"{key} names no usable version: {str(version)[:80]!r}")
    return version


def _recorded_etags(version_dir: Path) -> dict[str, str] | None:
    """The ETags a cached version was downloaded with; None when it is not cached."""
    if not version_dir.is_dir():
        return None
    try:
        etags = json.loads((version_dir / SOURCE).read_text()).get("etags")
    except (OSError, ValueError, AttributeError):
        return {}
    return {str(k): str(v) for k, v in etags.items()} if isinstance(etags, dict) else {}


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
        self._lock = threading.Lock()  # one sync at a time; held while a sync runs
        self._stop = threading.Event()
        self.last_attempt_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.last_failure_at: datetime | None = None
        self.last_error: str | None = None

    def _key(self, name: str, *parts: str) -> str:
        return "/".join((f"{PREFIX}{name}", *parts))

    def sync_once(self) -> list[str]:
        """One pass over the served models; the names whose served files changed. Never
        raises: an error is logged, recorded, and the cache keeps what it had."""
        with self._lock:
            started = self._wallclock()
            self.last_attempt_at = started
            deadline = self._clock() + self.deadline_s

            def expired() -> bool:
                return self._stop.is_set() or self._clock() > deadline

            changed: list[str] = []
            errors: list[str] = []
            for name, artefacts in self.models.items():
                try:
                    if self._sync_model(name, artefacts, expired):
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

    def _sync_model(self, name: str, artefacts: Artefacts, expired: Expired) -> bool:
        folder = self.cache_dir / name
        (folder / STORE).mkdir(parents=True, exist_ok=True)
        self._remove_leftovers(folder)
        key = self._key(name, POINTER)
        incoming = folder / f".{POINTER}.{uuid.uuid4().hex}.part"
        try:
            if (
                self.reader.fetch(key, incoming, max_bytes=POINTER_MAX_BYTES, expired=expired)
                is None
            ):
                log.info("model %s: nothing promoted in the registry yet", name)
                return False
            version = _pointed_version(incoming.read_bytes(), key)
            current = local_version(folder)
            recorded = _recorded_etags(folder / version)
            fresh = recorded is not None and self._unchanged(
                name, version, artefacts, recorded, expired
            )
            if version == current and fresh:
                return False
            if not fresh:
                self._download(name, version, artefacts, folder, expired)
            os.replace(incoming, folder / POINTER)
            self._prune(folder, keep={version, current})
            log.info("model %s: serving %s from the registry (was %s)", name, version, current)
            return True
        finally:
            incoming.unlink(missing_ok=True)

    def _unchanged(
        self,
        name: str,
        version: str,
        artefacts: Artefacts,
        recorded: dict[str, str],
        expired: Expired,
    ) -> bool:
        """Whether the cached copy of `version` is what the registry holds now: versions are
        not immutable (a training run can overwrite one), so the required files' ETags are
        compared (one HEAD each)."""
        for file in artefacts.required:
            key = self._key(name, version, file)
            etag = self.reader.etag(key, expired=expired)
            if etag is None:
                raise RegistryError(f"{key} is missing")
            if etag != recorded.get(file):
                log.info("model %s: %s changed in the registry; downloading it again", name, key)
                return False
        return True

    def _download(
        self, name: str, version: str, artefacts: Artefacts, folder: Path, expired: Expired
    ) -> None:
        """Into a fresh folder under `.v/`, then the `<version>` symlink is switched to it with
        one atomic rename: the old files stay readable until then, new ones are complete."""
        staging = Path(tempfile.mkdtemp(prefix=f"{version}.", dir=folder / STORE))
        committed = False
        try:
            etags: dict[str, str] = {}
            for file in artefacts.required:
                key = self._key(name, version, file)
                etag = self.reader.fetch(
                    key, staging / file, max_bytes=ARTEFACT_MAX_BYTES, expired=expired
                )
                if etag is None:
                    raise RegistryError(f"{key} is missing")
                etags[file] = etag
            for file in artefacts.optional:
                key = self._key(name, version, file)
                etag = self.reader.fetch(
                    key, staging / file, max_bytes=ARTEFACT_MAX_BYTES, expired=expired
                )
                if etag is None:
                    (staging / file).unlink(missing_ok=True)
                else:
                    etags[file] = etag
            (staging / SOURCE).write_text(json.dumps({"version": version, "etags": etags}))
            staging.chmod(0o755)
            link = folder / version
            if link.is_dir() and not link.is_symlink():  # not ours: replaced, not followed
                shutil.rmtree(link)
            switch = folder / f".link-{uuid.uuid4().hex}"
            switch.symlink_to(Path(STORE) / staging.name)
            os.replace(switch, link)
            committed = True
        finally:
            if not committed:
                shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _remove_leftovers(folder: Path) -> None:
        """What an interrupted sync left behind, and names that must not be folders (never
        served: the ranker follows only `latest.json` and refuses versions starting with a dot)."""
        for child in folder.iterdir():
            if child.name == POINTER and child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            elif child.name.startswith(".link-") or (
                child.name.startswith(f".{POINTER}.") and child.name.endswith(".part")
            ):
                child.unlink(missing_ok=True)
        linked = {Path(os.readlink(child)).name for child in folder.iterdir() if child.is_symlink()}
        for stored in (folder / STORE).iterdir():  # a download a crash left half-done
            if stored.name not in linked:
                shutil.rmtree(stored, ignore_errors=True)

    @staticmethod
    def _prune(folder: Path, keep: set[str | None]) -> None:
        """Keep the current and the previous version; drop other links and unreferenced files."""
        targets: set[str] = set()
        for child in folder.iterdir():
            if child.name.startswith(".") or child.name == POINTER:
                continue
            if child.name in keep and child.is_symlink():
                targets.add(Path(os.readlink(child)).name)
            elif child.is_symlink():
                child.unlink()
            elif child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
        for stored in (folder / STORE).iterdir():
            if stored.name not in targets:
                shutil.rmtree(stored, ignore_errors=True)

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
        """Sync now, then every `interval_s`, off the event loop; until stopped or cancelled."""
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self.sync_once)
            except Exception:  # sync_once does not raise; belt and braces for the loop
                log.exception("the model sync failed")
            await asyncio.sleep(interval_s)

    def stop(self) -> None:
        """Ask a running sync to give up at its next check (each piece read, each request)."""
        self._stop.set()

    def wait_idle(self, timeout_s: float) -> bool:
        """Wait until no sync is running (True), at most `timeout_s`."""
        if self._lock.acquire(timeout=timeout_s):
            self._lock.release()
            return True
        return False

    def close(self) -> None:
        self.reader.close()
