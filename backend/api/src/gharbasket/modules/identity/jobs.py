"""Scheduled jobs of M1 (spec §6), registered with the platform's jobs runner (ADR-0021).

`build_data_export` builds the queued exports (spec §3.3). The spec names it the consumer of
`identity.data_export_requested`, but outbox consumers get no object storage, so it is a job that
takes queued rows instead, every few minutes from cron-job.org: well inside the promised 24 hours.
Once the file is written it publishes `identity.data_export_ready` for M11's "your data is ready"
notification, which never carries the link.

The captain role follows M9's appointments: `community.captain_assigned` grants
`captain:<society>` and `community.captain_ended` removes it (ADR-0026 §4).
"""

import json
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity import deletion, events, service
from gharbasket.modules.identity.exports import KEEP, MAX_ATTEMPTS
from gharbasket.modules.identity.models import DataExport, Deletion
from gharbasket.modules.identity.partner_roles import sync_partner_role
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.notifications import api as notifications_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.platform import files
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish, subscribe

FORMAT = "gharbasket-export/v1"
BATCH = 20
# Each module's own section, read-only (M1 §3.3). New modules add theirs when they ship.
SECTIONS: dict[str, Callable[[AsyncSession, uuid.UUID], Awaitable[dict[str, Any]]]] = {
    "identity": identity_api.export_for_user,
    "subscriptions": subscriptions_api.export_for_user,
    "orders": orders_api.export_for_user,
    "billing": billing_api.export_for_user,
    "notifications": notifications_api.export_for_user,
    "partners": partners_api.export_for_user,
    "community": community_api.export_for_user,
    "logistics": logistics_api.export_for_user,
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


# --- the captain role from M9's appointments (ADR-0026 §4) --------------------------------------
# Each consumer reads the appointment's current state through community.api, so a redelivered or
# late event cannot grant a role that has ended or remove one that is active again.


def _captain_target(event: OutboxEvent) -> tuple[uuid.UUID, uuid.UUID] | None:
    try:
        return uuid.UUID(str(event.payload["user_id"])), uuid.UUID(str(event.payload["society_id"]))
    except (KeyError, ValueError):
        return None


async def on_captain_assigned(session: AsyncSession, event: OutboxEvent) -> None:
    target = _captain_target(event)
    if target is None or not await community_api.is_active_captain(session, *target):
        return
    await service.grant_system_role(session, target[0], "captain", target[1])


async def on_captain_ended(session: AsyncSession, event: OutboxEvent) -> None:
    target = _captain_target(event)
    if target is None or await community_api.is_active_captain(session, *target):
        return
    await service.revoke_system_role(session, target[0], "captain", target[1])


subscribe(community_api.CAPTAIN_ASSIGNED, on_captain_assigned)
subscribe(community_api.CAPTAIN_ENDED, on_captain_ended)

# Hub roles of kitchens, riders and packers follow M8 (partner_roles.py; ADR-0026 §4)
for _event in (partners_api.VERIFIED, partners_api.SUSPENDED, partners_api.REINSTATED):
    subscribe(_event, sync_partner_role)


# --- the purge of deleted accounts (spec §3.5) --------------------------------------------------

PURGE_BATCH = 50
OVERDUE = timedelta(days=15)
Eraser = Callable[[AsyncSession, uuid.UUID, uuid.UUID | None], Awaitable[dict[str, int]]]
# Each module erases its own data through its api.py, in the fixed order of spec §3.5, and
# returns {table: rows} for the tombstone. New modules add theirs when they ship; the platform's
# and M1's own rows always go last.
ERASERS: list[tuple[str, Eraser]] = [
    ("notifications", notifications_api.delete_for_user),
    ("intel_gateway", intel_api.delete_for_user),
    ("community", community_api.anonymise_for_user),
    ("hub_ops", hub_ops_api.anonymise_for_user),
    ("logistics", logistics_api.anonymise_for_user),
    ("subscriptions", subscriptions_api.delete_for_user),
    ("orders", orders_api.anonymise_for_user),
    ("billing", billing_api.anonymise_for_user),
    ("platform", deletion.erase_platform),
    ("identity", deletion.erase_identity),
]


async def _blockers(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> list[str]:
    """Why the purge must wait (spec §3.5 step 1), read through each module's facade."""
    reasons = []
    if household_id is not None and await orders_api.has_open_orders(session, household_id):
        reasons.append("open_orders")
    if household_id is not None and await orders_api.has_open_issues(session, household_id):
        reasons.append("open_issues")
    if await billing_api.balance(session, user_id) != 0:
        reasons.append("wallet_not_closed")
    return reasons


async def purge(session: AsyncSession, gone: Deletion, now: datetime) -> list[str]:
    """Erase one account, or record why it must wait; the caller owns the savepoint."""
    household_id = await deletion.household_id_of(session, gone.user_id)
    gone.blocked_reasons = await _blockers(session, gone.user_id, household_id)
    if gone.blocked_reasons:
        return gone.blocked_reasons
    gone.counts = {
        module: await erase(session, gone.user_id, household_id) for module, erase in ERASERS
    }
    gone.purged_at = now
    household = None if household_id is None else str(household_id)
    payload = {"user_id": str(gone.user_id), "household_id": household}
    await publish(session, events.USER_DELETED, payload)
    return []


@job("purge_deleted_users", "Erase accounts whose 30-day deletion grace is over, module by module")
async def purge_deleted_users(ctx: JobContext) -> dict[str, Any]:
    # Never later than now: a run dated in the future must not purge before the grace ends.
    today = min(ctx.scheduled_for, datetime.now(UTC)).astimezone(deletion.IST).date()
    due = await ctx.session.scalars(
        deletion.purge_due(today).limit(PURGE_BATCH).with_for_update(skip_locked=True)
    )
    summary: dict[str, Any] = {"purged": 0, "blocked": {}, "overdue": [], "failed": {}}
    for gone in due.all():
        user = str(gone.user_id)  # the tombstone's id: it leads to nobody once purged
        try:
            async with ctx.session.begin_nested():  # one account's failure stops only that one
                reasons = await purge(ctx.session, gone, datetime.now(UTC))
        except Exception as exc:
            await ctx.session.refresh(gone)  # the savepoint's rollback expired it
            gone.last_attempt_on = today
            summary["failed"][user] = type(exc).__name__
            continue
        gone.last_attempt_on = today
        if not reasons:
            summary["purged"] += 1
            continue
        summary["blocked"][user] = reasons
        if gone.purge_on + OVERDUE < today:
            summary["overdue"].append(user)
    return summary
