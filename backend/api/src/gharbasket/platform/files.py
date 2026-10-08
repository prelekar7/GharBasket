"""Private uploads straight to object storage (M0 §3.1). The API never streams file bytes.

    POST /v1/files/presign {purpose, content_type, size_bytes} → {key, url, method, headers, ...}

The app PUTs the bytes to `url` and sends only `key` to the module that uses it. That module, in
its own transaction:

    upload = await files.claim(session, key, user_id=me, purpose="order_issue_photo", by="orders")
    if upload is None: raise ProblemError(422, "orders.photo_invalid", ...)
    info = await storage.head(key)        # the object really is there, with the signed size/type
    ...
    await files.set_delete_after(session, key, resolved_at + timedelta(days=90))

and reads back only through `storage.signed_get(key)` (5 minutes), after its own permission
check. `sweep_uploads` (platform/jobs.py) deletes what was never claimed and what has expired.
"""

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import Upload
from gharbasket.platform.settings import Settings
from gharbasket.platform.valkey import Valkey

log = structlog.get_logger("gharbasket.files")

PUT_TTL = timedelta(minutes=10)
GET_TTL = timedelta(minutes=5)
UNCLAIMED_GRACE = timedelta(hours=24)
PRESIGN_LIMIT = (30, 600)  # per user: 30 per 10 minutes
MB = 1024 * 1024
EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


@dataclass(frozen=True)
class Purpose:
    types: tuple[str, ...]
    max_bytes: int


_IMAGES = ("image/jpeg", "image/png", "image/webp")
PURPOSES = {
    "order_issue_photo": Purpose(_IMAGES, 5 * MB),
    "donation_photo": Purpose(_IMAGES, 5 * MB),
    "kyc_document": Purpose(("image/jpeg", "image/png", "application/pdf"), 10 * MB),
}  # data_export is written by the server (M1), never presigned


def storage_unavailable() -> ProblemError:
    return ProblemError(
        503, "platform.storage_unavailable", "File uploads are not available right now."
    )


@dataclass(frozen=True)
class ObjectInfo:
    content_type: str
    size_bytes: int


class ObjectStore:
    """The private bucket. `client` talks to storage; `public` only signs URLs for the apps."""

    def __init__(self, client: Any, public: Any, bucket: str) -> None:
        self.client, self.public, self.bucket = client, public, bucket

    def presign_put(self, key: str, content_type: str, size_bytes: int) -> str:
        # Content-Type and Content-Length are signed: a different type or size is refused.
        params = {
            "Bucket": self.bucket,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": size_bytes,
        }
        return self.public.generate_presigned_url(
            "put_object", Params=params, ExpiresIn=int(PUT_TTL.total_seconds()), HttpMethod="PUT"
        )

    def signed_get(self, key: str) -> str:
        """A 5-minute read link; mint it only after the caller's own permission check."""
        return self.public.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=int(GET_TTL.total_seconds()),
        )

    async def head(self, key: str) -> ObjectInfo | None:
        """The stored object's type and size, or None when nothing was uploaded."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            found = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise storage_unavailable() from exc
        except BotoCoreError as exc:
            raise storage_unavailable() from exc
        return ObjectInfo(found["ContentType"], int(found["ContentLength"]))

    async def delete(self, keys: Sequence[str]) -> None:
        """Delete objects; a key that is already gone is not an error (S3 semantics)."""
        for start in range(0, len(keys), 1000):  # DeleteObjects takes at most 1,000 keys
            chunk = [{"Key": key} for key in keys[start : start + 1000]]
            answer = await asyncio.to_thread(
                self.client.delete_objects,
                Bucket=self.bucket,
                Delete={"Objects": chunk, "Quiet": True},
            )
            if answer.get("Errors"):
                raise RuntimeError(f"storage refused {len(answer['Errors'])} deletes")


def create_store(settings: Settings) -> ObjectStore | None:
    if not settings.storage_enabled:
        return None
    import boto3  # heavy; only when storage is configured
    from botocore.config import Config

    assert settings.s3_secret_key is not None  # storage_enabled
    secret = settings.s3_secret_key.get_secret_value()
    config = Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},  # SeaweedFS and R2 both take path-style URLs
        connect_timeout=3,
        read_timeout=10,
        retries={"max_attempts": 2},
    )

    def client(endpoint: str | None) -> Any:
        return boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=secret,
            region_name=settings.s3_region,
            config=config,
        )

    internal = client(settings.s3_endpoint_url)
    public = client(settings.s3_public_endpoint_url) if settings.s3_public_endpoint_url else None
    return ObjectStore(internal, public or internal, settings.s3_bucket_private)


def _app_store(request: Request) -> ObjectStore:
    store = getattr(request.app.state, "storage", None)
    if store is None:
        raise storage_unavailable()
    return store


# For owning modules' routes: `storage: Storage` then `storage.signed_get(key)` / `.head(key)`.
Storage = Annotated[ObjectStore, Depends(_app_store)]


# --- the facade for owning modules -------------------------------------------------------------


async def verify(
    session: AsyncSession, key: str, *, user_id: uuid.UUID, purpose: str
) -> Upload | None:
    """The upload behind `key` if `user_id` presigned it for `purpose`; else None."""
    upload = await session.get(Upload, key)
    if upload is None or upload.user_id != user_id or upload.purpose != purpose:
        return None
    return upload


async def claim(
    session: AsyncSession, key: str, *, user_id: uuid.UUID, purpose: str, by: str
) -> Upload | None:
    """Mark the uploader's own key of `purpose` as kept by module `by`, in the caller's
    transaction. None when it is unknown, someone else's, another purpose, or another module's.
    Claiming again for the same module is harmless (a retried request)."""
    upload = await session.get(Upload, key, with_for_update=True)
    if upload is None or upload.user_id != user_id or upload.purpose != purpose:
        return None
    if upload.claimed_by is None:
        upload.claimed_by, upload.claimed_at = by, datetime.now(UTC)
        await session.flush()
    return upload if upload.claimed_by == by else None


async def set_delete_after(session: AsyncSession, key: str, at: datetime | None) -> bool:
    """When sweep_uploads may delete a claimed upload (None keeps it). False if not claimed."""
    upload = await session.get(Upload, key, with_for_update=True)
    if upload is None or upload.claimed_by is None:
        return False
    upload.delete_after = at
    await session.flush()
    return True


async def sweep(
    session: AsyncSession, store: ObjectStore, *, now: datetime, batch: int = 500
) -> dict[str, int]:
    """Delete uploads never claimed within 24 h of `now`, and claimed ones past delete_after.

    Objects go first, then rows: if the transaction fails, the next run deletes them again."""
    unclaimed = and_(Upload.claimed_at.is_(None), Upload.created_at < now - UNCLAIMED_GRACE)
    counts = {"unclaimed": 0, "expired": 0}
    while True:
        rows = (
            await session.execute(
                select(Upload.key, Upload.claimed_at)
                .where(or_(unclaimed, Upload.delete_after <= now))
                .order_by(Upload.key)
                .limit(batch)
                .with_for_update(skip_locked=True)
            )
        ).all()
        if not rows:
            return counts
        keys = [row.key for row in rows]
        await store.delete(keys)
        await session.execute(delete(Upload).where(Upload.key.in_(keys)))
        for row in rows:
            counts["unclaimed" if row.claimed_at is None else "expired"] += 1


# --- HTTP ---------------------------------------------------------------------------------------

router = APIRouter(tags=["files"])


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


class PresignIn(BaseModel):
    purpose: str = Field(
        max_length=40, description="order_issue_photo, donation_photo or kyc_document"
    )
    content_type: str = Field(max_length=100, examples=["image/jpeg"])
    size_bytes: int = Field(description="The exact size of the file to upload", examples=[834221])


class PresignOut(BaseModel):
    key: str = Field(description="Send only this to the module that uses the file")
    url: str = Field(description="PUT the bytes here before expires_at")
    method: Literal["PUT"]
    headers: dict[str, str] = Field(description="Send exactly these headers on the PUT")
    expires_at: datetime


@router.post(
    "/files/presign",
    summary="A presigned PUT URL for one private upload (M0 §3.1)",
    responses={
        422: _problem(
            "platform.upload_purpose_invalid, platform.upload_type_not_allowed (with allowed) "
            "or platform.upload_too_large (with max_bytes)"
        ),
        429: _problem("platform.upload_rate_limited (see Retry-After)"),
        503: _problem("platform.storage_unavailable"),
    },
)
async def presign(
    body: PresignIn,
    request: Request,
    principal: CurrentPrincipal,
    session: DbSession,
    valkey: Valkey,
) -> PresignOut:
    await rate_limit.enforce(
        valkey,
        f"presign:user:{principal.user_id}",
        limit=PRESIGN_LIMIT[0],
        window_s=PRESIGN_LIMIT[1],
        code="platform.upload_rate_limited",
        detail="Too many uploads. Wait a few minutes and try again.",
    )
    purpose = PURPOSES.get(body.purpose)
    if purpose is None:
        raise ProblemError(422, "platform.upload_purpose_invalid", "This upload is not allowed.")
    content_type = body.content_type.strip().lower()
    if content_type not in purpose.types:
        raise ProblemError(
            422,
            "platform.upload_type_not_allowed",
            "This kind of file is not allowed here.",
            extra={"allowed": list(purpose.types)},
        )
    if not 0 < body.size_bytes <= purpose.max_bytes:
        raise ProblemError(
            422,
            "platform.upload_too_large",
            f"The file must be at most {purpose.max_bytes // MB} MB.",
            extra={"max_bytes": purpose.max_bytes},
        )
    store = _app_store(request)

    file_id = (await session.execute(select(func.uuidv7()))).scalar_one()
    key = f"{body.purpose}/{principal.user_id}/{file_id}.{EXTENSIONS[content_type]}"
    expires_at = datetime.now(UTC).replace(microsecond=0) + PUT_TTL
    url = store.presign_put(key, content_type, body.size_bytes)
    session.add(
        Upload(
            key=key,
            user_id=principal.user_id,
            purpose=body.purpose,
            content_type=content_type,
            size_bytes=body.size_bytes,
        )
    )
    await session.commit()
    log.info("upload_presigned", purpose=body.purpose, size_bytes=body.size_bytes)
    return PresignOut(
        key=key,
        url=url,
        method="PUT",
        headers={"Content-Type": content_type, "Content-Length": str(body.size_bytes)},
        expires_at=expires_at,
    )
