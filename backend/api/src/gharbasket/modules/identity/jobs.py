"""Scheduled jobs of M1 (spec §6), registered with the platform's jobs runner (ADR-0021).

`build_data_export` builds the queued exports (spec §3.3). The spec names it the consumer of
`identity.data_export_requested`, but outbox consumers get no object storage, so it is a job that
takes queued rows instead, every few minutes from cron-job.org: well inside the promised 24 hours.
Once the file is written it publishes `identity.data_export_ready` for M11's "your data is ready"
notification, which never carries the link.
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity import events
from gharbasket.modules.identity.exports import KEEP, MAX_ATTEMPTS
from gharbasket.modules.identity.models import DataExport
from gharbasket.modules.notifications import api as notifications_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.platform import files
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.outbox import publish

FORMAT = "gharbasket-export/v1"
BATCH = 20
# Each module's own section, read-only (M1 §3.3). New modules add theirs when they ship.
SECTIONS: dict[str, Callable[[AsyncSession, uuid.UUID], Awaitable[dict[str, Any]]]] = {
    "identity": identity_api.export_for_user,
    "subscriptions": subscriptions_api.export_for_user,
    "orders": orders_api.export_for_user,
    "billing": billing_api.export_for_user,
    "notifications": notifications_api.export_for_user,
}


def _plain(value: object) -> object:
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return str(value)  # UUIDs


async def build(session: AsyncSession, store: files.ObjectStore, export: DataExport) -> None:
    """Write the export's JSON file and mark it ready; the caller owns the transaction."""
    now = datetime.now(UTC)
    sections = {name: await section(session, export.user_id) for name, section in SECTIONS.items()}
    document = {
        "format": FORMAT,
        "generated_at": now,
        "user_id": export.user_id,
        "sections": sections,
    }
    body = json.dumps(document, default=_plain, ensure_ascii=False, indent=1).encode()
    key = f"data_export/{export.user_id}/{export.id}.json"
    expires_at = now + KEEP
    # Recorded before the put: sweep_uploads deletes the file once it expires.
    await files.record(
        session,
        key,
        user_id=export.user_id,
        purpose="data_export",
        content_type="application/json",
        size_bytes=len(body),
        by="identity",
        delete_after=expires_at,
    )
    await store.put(key, body, "application/json")
    export.status, export.object_key, export.size_bytes = "ready", key, len(body)
    export.ready_at, export.expires_at, export.error = now, expires_at, None
    await publish(
        session,
        events.DATA_EXPORT_READY,
        {"export_id": str(export.id), "user_id": str(export.user_id)},
    )


@job("build_data_export", "Build queued data exports: one JSON file each, kept 7 days")
async def build_data_export(ctx: JobContext) -> dict[str, Any]:
    if ctx.storage is None:
        raise RuntimeError("object storage is not configured (S3_ENDPOINT_URL)")
    queued = await ctx.session.execute(
        select(DataExport)
        .where(DataExport.status == "queued")
        .order_by(DataExport.requested_at)
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    )
    counts = {"ready": 0, "retry": 0, "failed": 0}
    for export in queued.scalars().all():
        try:
            async with ctx.session.begin_nested():
                await build(ctx.session, ctx.storage, export)
        except Exception as exc:
            await ctx.session.refresh(export)  # the savepoint's rollback expired it
            export.attempts += 1
            export.error = f"{type(exc).__name__}: {exc}"[:500]
            if export.attempts >= MAX_ATTEMPTS:
                export.status = "failed"  # asking again the same day queues it again
            counts["failed" if export.status == "failed" else "retry"] += 1
            continue
        counts["ready"] += 1
    return counts


@job("expire_data_exports", "Mark data exports past their 7 days expired")
async def expire_data_exports(ctx: JobContext) -> dict[str, Any]:
    # The file itself goes with sweep_uploads (its delete_after is the same expiry).
    expired = await ctx.session.execute(
        update(DataExport)
        .where(DataExport.status == "ready", DataExport.expires_at <= ctx.scheduled_for)
        .values(status="expired")
        .returning(DataExport.id)
    )
    return {"expired": len(expired.all())}
