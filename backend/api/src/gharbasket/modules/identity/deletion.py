"""Account deletion (spec §3.5, handbook 13 §13.6): the DPDP right to erasure.

`DELETE /v1/me` marks the account `deleting`, signs out every device, deletes pending data
exports and publishes `identity.deletion_requested`. Signing in again within 30 days, confirmed on
the app's "Keep my account?" screen, cancels it. The purge after the 30 days comes next.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import events, service
from gharbasket.modules.identity.models import (
    DataExport,
    Deletion,
    Household,
    LoginSession,
    User,
    UserRole,
)
from gharbasket.platform import audit, files
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

IST = ZoneInfo("Asia/Kolkata")
GRACE = timedelta(days=30)
FRESH_OTP = timedelta(minutes=5)
# Staff roles another admin removes first (spec §3.5); partner and captain roles end by events.
STAFF_ROLES = ("admin", "hub_manager", "franchise_owner", "ngo_coordinator", "catalog_editor")


async def pending(session: AsyncSession, user_id: uuid.UUID) -> Deletion | None:
    """The user's scheduled deletion: asked for, neither cancelled nor purged."""
    return (
        await session.execute(
            select(Deletion).where(
                Deletion.user_id == user_id,
                Deletion.cancelled_at.is_(None),
                Deletion.purged_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def household_id_of(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(select(Household.id).where(Household.owner_user_id == user_id))


async def request(
    session: AsyncSession, user: User, login: LoginSession, *, now: datetime | None = None
) -> tuple[Deletion, list[uuid.UUID]]:
    """Schedule the deletion in the caller's transaction (spec §3.5 steps 1 to 5); returns it and
    the ids of the sessions it ended, for the Valkey deny-list after the commit."""
    at = now or datetime.now(UTC)
    staff = sorted(
        {
            role
            for role in await session.scalars(
                select(UserRole.role).where(
                    UserRole.user_id == user.id,
                    UserRole.role.in_(STAFF_ROLES),
                    UserRole.status == "verified",
                )
            )
        }
    )
    if staff:
        raise ProblemError(
            409,
            "identity.staff_account",
            "This account has staff roles. Ask an admin to remove them first.",
            extra={"roles": staff},
        )
    if at - login.auth_time > FRESH_OTP:
        raise ProblemError(
            403,
            "identity.fresh_otp_required",
            "Enter the code sent to your phone again to delete your account.",
            extra={"max_age_s": int(FRESH_OTP.total_seconds())},
        )

    purge_on = at.astimezone(IST).date() + GRACE
    # A row from an earlier, cancelled request is reused: user_id is the key.
    values: dict[str, Any] = {
        "requested_at": at,
        "purge_on": purge_on,
        "cancelled_at": None,
        "purged_at": None,
        "blocked_reasons": [],
        "counts": None,
    }
    await session.execute(
        insert(Deletion)
        .values(user_id=user.id, **values)
        .on_conflict_do_update(index_elements=[Deletion.user_id], set_=values)
    )
    user.status = "deleting"
    ended = await service.revoke_all_sessions(session, user.id, reason="account_deleted", now=at)

    exports = (await session.scalars(select(DataExport).where(DataExport.user_id == user.id))).all()
    for export in exports:
        if export.object_key is not None:
            await files.set_delete_after(session, export.object_key, at)  # sweep_uploads
        await session.delete(export)

    household_id = await household_id_of(session, user.id)
    await publish(
        session,
        events.DELETION_REQUESTED,
        {
            "user_id": str(user.id),
            "household_id": None if household_id is None else str(household_id),
            "purge_on": purge_on.isoformat(),
        },
    )
    await audit.record(
        session,
        actor_user_id=user.id,
        action="identity.deletion_requested",
        entity_type="user",
        entity_id=str(user.id),
    )
    await session.flush()
    deletion = await session.get(Deletion, user.id, populate_existing=True)
    assert deletion is not None
    return deletion, ended


async def cancel(session: AsyncSession, user: User, deletion: Deletion | None) -> None:
    """The customer signed in again and chose "Keep my account" (spec §3.5). Nothing that
    already happened (the ended subscription, the refund, roles) is undone."""
    at = datetime.now(UTC)
    user.status = "active"
    if deletion is not None:
        deletion.cancelled_at = at
    await publish(session, events.DELETION_CANCELLED, {"user_id": str(user.id)})
    await audit.record(
        session,
        actor_user_id=user.id,
        action="identity.deletion_cancelled",
        entity_type="user",
        entity_id=str(user.id),
    )
