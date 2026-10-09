"""Download my data (spec §3.3, handbook 13 §13.6): ask, build in the background, poll, download.

`POST /v1/me/data-export` makes today's export or answers it: one per user per IST day, so the
day is the idempotency key. `build_data_export` (jobs.py) puts each module's `export_for_user`
section into one JSON file in the private bucket, kept 7 days. `GET` answers the latest export
and, while it is ready, a fresh 5-minute download link. Requests and links are audit-logged.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import events
from gharbasket.modules.identity.models import DataExport
from gharbasket.modules.identity.schemas import DataExportDownload, DataExportOut
from gharbasket.platform import audit, files, rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.valkey import Valkey

IST = ZoneInfo("Asia/Kolkata")
IST_OFFSET_S = 19800  # 05:30: the request cap resets at IST midnight
KEEP = timedelta(days=7)
READY_WITHIN_HOURS = 24
REQUESTS_PER_DAY = 5
MAX_ATTEMPTS = 3


def status_of(export: DataExport, now: datetime) -> str:
    """`ready` past its expiry reads as `expired` before expire_data_exports marks it."""
    if export.status == "ready" and export.expires_at is not None and export.expires_at <= now:
        return "expired"
    return export.status


async def request_export(session: AsyncSession, user_id: uuid.UUID) -> DataExport:
    """Today's export (IST), made if new and queued again if it failed; in the caller's
    transaction. A new or re-queued export publishes identity.data_export_requested."""
    today = datetime.now(IST).date()
    inserted = await session.execute(
        insert(DataExport)
        .values(user_id=user_id, requested_on=today)
        .on_conflict_do_nothing(index_elements=["user_id", "requested_on"])
        .returning(DataExport.id)
    )
    fresh = inserted.scalar_one_or_none() is not None
    export = (
        await session.execute(
            select(DataExport)
            .where(DataExport.user_id == user_id, DataExport.requested_on == today)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if export.status == "failed":
        export.status, export.attempts, export.error = "queued", 0, None
        fresh = True
    if fresh:
        payload = {"export_id": str(export.id), "user_id": str(user_id)}
        await publish(session, events.DATA_EXPORT_REQUESTED, payload)
    await audit.record(
        session,
        actor_user_id=user_id,
        action="identity.data_export_requested",
        entity_type="data_export",
        entity_id=str(export.id),
    )
    return export


async def latest(session: AsyncSession, user_id: uuid.UUID) -> DataExport | None:
    return (
        await session.execute(
            select(DataExport)
            .where(DataExport.user_id == user_id)
            .order_by(DataExport.requested_on.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def view(export: DataExport, now: datetime, download: DataExportDownload | None) -> DataExportOut:
    return DataExportOut.model_validate(
        {
            "id": export.id,
            "status": status_of(export, now),
            "requested_at": export.requested_at,
            "ready_at": export.ready_at,
            "expires_at": export.expires_at,
            "ready_within_hours": READY_WITHIN_HOURS,
            "download": download,
        }
    )


# --- HTTP ---------------------------------------------------------------------------------------

router = APIRouter(tags=["identity"])


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


@router.post(
    "/me/data-export",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ask for a copy of your data (one per IST day; asking again answers that export)",
    responses={
        401: _problem("auth.invalid_token"),
        429: _problem("identity.export_rate_limited (more than 5 a day; see Retry-After)"),
    },
)
async def ask_for_export(
    principal: CurrentPrincipal, session: DbSession, valkey: Valkey
) -> DataExportOut:
    await rate_limit.enforce(
        valkey,
        f"data_export:user:{principal.user_id}",
        limit=REQUESTS_PER_DAY,
        window_s=86400,
        offset_s=IST_OFFSET_S,
        code="identity.export_rate_limited",
        detail="You have asked for your data many times today. Try again tomorrow.",
    )
    export = await request_export(session, principal.user_id)
    await session.commit()
    return view(export, datetime.now(UTC), None)


@router.get(
    "/me/data-export",
    summary="Your latest data export, with a fresh 5-minute download link while it is ready",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("identity.export_not_found"),
        503: _problem("platform.storage_unavailable"),
    },
)
async def my_export(
    principal: CurrentPrincipal, request: Request, session: DbSession
) -> DataExportOut:
    export = await latest(session, principal.user_id)
    if export is None:
        raise ProblemError(404, "identity.export_not_found", "You have not asked for your data.")
    now = datetime.now(UTC)
    download = None
    if status_of(export, now) == "ready" and export.object_key and export.size_bytes:
        store: files.ObjectStore | None = getattr(request.app.state, "storage", None)
        if store is None:
            raise files.storage_unavailable()
        download = DataExportDownload(
            url=store.signed_get(export.object_key),
            expires_at=now.replace(microsecond=0) + files.GET_TTL,
            size_bytes=export.size_bytes,
        )
        await audit.record(
            session,
            actor_user_id=principal.user_id,
            action="identity.data_export_link_minted",
            entity_type="data_export",
            entity_id=str(export.id),
        )
        await session.commit()
    return view(export, now, download)
