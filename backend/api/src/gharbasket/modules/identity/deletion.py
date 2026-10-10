"""Account deletion (spec §3.5, handbook 13 §13.6): the DPDP right to erasure.

`DELETE /v1/me` marks the account `deleting`, signs out every device, deletes pending data
exports and publishes `identity.deletion_requested`. Signing in again within 30 days, confirmed on
the app's "Keep my account?" screen, cancels it. After that, `purge_deleted_users` (jobs.py) has
each module erase its own data; `erase_platform` and `erase_identity` below are the last two
steps (spec §3.5 order 10 and 11).
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import events, service
from gharbasket.modules.identity.models import (
    Address,
    Consent,
    DataExport,
    Deletion,
    Household,
    HouseholdMember,
    LoginSession,
    RefreshToken,
    User,
    UserRole,
)
from gharbasket.platform import audit, files, idempotency
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


async def for_sign_in(session: AsyncSession, user_id: uuid.UUID) -> Deletion:
    """The scheduled deletion of an account that is signing in, locked: a purge running at the
    same moment holds the same row, so the sign-in waits for it and then sees it purged (409
    identity.account_deleted) instead of keeping an account that is being erased."""
    found = await session.scalar(
        select(Deletion)
        .where(Deletion.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if found is None or found.purged_at is not None or found.cancelled_at is not None:
        raise ProblemError(
            409,
            "identity.account_deleted",
            "This account has just been deleted. Sign in again to start a new one.",
        )
    return found


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
        "last_attempt_on": None,
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


async def cancel(session: AsyncSession, user: User, deletion: Deletion) -> None:
    """The customer signed in again and chose "Keep my account" (spec §3.5). Nothing that
    already happened (the ended subscription, the refund, roles) is undone."""
    at = datetime.now(UTC)
    user.status, deletion.cancelled_at = "active", at
    await publish(session, events.DELETION_CANCELLED, {"user_id": str(user.id)})
    await audit.record(
        session,
        actor_user_id=user.id,
        action="identity.deletion_cancelled",
        entity_type="user",
        entity_id=str(user.id),
    )


def purge_due(today: date) -> Any:
    """The open deletions whose grace period is over, oldest first."""
    return (
        select(Deletion)
        .where(
            Deletion.purge_on <= today,
            Deletion.cancelled_at.is_(None),
            Deletion.purged_at.is_(None),
        )
        .order_by(Deletion.requested_at)
    )


def _rows(result: Any) -> int:
    return result.rowcount  # pyright: ignore[reportAttributeAccessIssue]


async def erase_platform(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M0's part (spec §3.5 order 10): the user's stored idempotent responses go, and every
    upload under */<user_id>/ is due, so tonight's sweep_uploads deletes the objects and rows.
    The audit log stays (3 years; it holds no personal data)."""
    return {
        "idempotency_keys": await idempotency.forget_scope(session, str(user_id)),
        "uploads": await files.expire_for_user(session, user_id, datetime.now(UTC)),
    }


async def erase_identity(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's own rows, last (spec §3.5 order 11); children first, so each count is real."""
    sessions = select(LoginSession.id).where(LoginSession.user_id == user_id)
    counts = {
        "refresh_tokens": _rows(
            await session.execute(delete(RefreshToken).where(RefreshToken.session_id.in_(sessions)))
        ),
        "sessions": _rows(
            await session.execute(delete(LoginSession).where(LoginSession.user_id == user_id))
        ),
        "user_roles": _rows(
            await session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        ),
        "consents": _rows(await session.execute(delete(Consent).where(Consent.user_id == user_id))),
        "data_exports": _rows(
            await session.execute(delete(DataExport).where(DataExport.user_id == user_id))
        ),
        "household_members": 0,
        "addresses": 0,
        "households": 0,
    }
    if household_id is not None:
        counts["household_members"] = _rows(
            await session.execute(
                delete(HouseholdMember).where(HouseholdMember.household_id == household_id)
            )
        )
        counts["addresses"] = _rows(
            await session.execute(delete(Address).where(Address.household_id == household_id))
        )
        counts["households"] = _rows(
            await session.execute(delete(Household).where(Household.id == household_id))
        )
    counts["users"] = _rows(await session.execute(delete(User).where(User.id == user_id)))
    return counts
