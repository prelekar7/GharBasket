"""Business logic of M1 Identity & Household: accounts, sessions, refresh rotation (ADR-0024).

Sign-up is implicit: the first successful phone verification creates the account and grants the
`customer` role. Each device or browser gets a session; its refresh token is single-use and
rotates on every refresh:

- normal refresh: the presented token is marked used and a child token is issued;
- grace rule: if a used token is presented again within REUSE_GRACE and its child was never used
  (the response got lost, or two tabs refreshed at once), the unused child is revoked and a fresh
  one issued, so the user is not logged out;
- otherwise reuse means the token was copied: the whole session is revoked (`auth.refresh_reused`).
"""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import (
    PENDING_ROLES,
    LoginSession,
    PendingRoleGrant,
    RefreshToken,
    User,
    UserRole,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import ROLE_SCOPE
from gharbasket.platform.settings import Settings
from gharbasket.platform.tokens import invalid_token, issue_access_token

SESSION_LIFETIME = timedelta(days=30)
PENDING_GRANT_LIFETIME = timedelta(days=30)
REUSE_GRACE = timedelta(minutes=2)


class RefreshReused(ProblemError):
    """A copied refresh token was replayed; the session is already revoked and committed."""

    def __init__(self, session_id: uuid.UUID) -> None:
        super().__init__(
            401,
            "auth.refresh_reused",
            "This sign-in was used from somewhere else, so it has been ended. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
        self.session_id = session_id


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_expires_in: int
    refresh_token: str
    session_id: uuid.UUID
    session_expires_at: datetime
    user: User


def hash_refresh_token(settings: Settings, token: str) -> bytes:
    return hmac.new(settings.refresh_pepper, token.encode(), hashlib.sha256).digest()


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def find_or_create_user(session: AsyncSession, phone_e164: str) -> tuple[User, bool]:
    """Return the account for a verified phone number, creating it on first sign-in."""
    created = (
        await session.execute(
            insert(User)
            .values(phone_e164=phone_e164)
            .on_conflict_do_nothing(index_elements=[User.phone_e164])
            .returning(User.id)
        )
    ).scalar_one_or_none()
    user = (await session.execute(select(User).where(User.phone_e164 == phone_e164))).scalar_one()
    if created is not None:
        session.add(
            UserRole(
                user_id=user.id, role="customer", status="verified", verified_at=datetime.now(UTC)
            )
        )
        await session.flush()
        await claim_pending_grants(session, user)
    return user, created is not None


async def _lock_phone(session: AsyncSession, phone_e164: str) -> None:
    """Serialise "an admin gives a role to this number" with "this number signs up": whoever
    comes second sees the other's committed work, so a pending grant is never left behind."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"identity.phone:{phone_e164}"},
    )


async def claim_pending_grants(session: AsyncSession, user: User) -> None:
    """The first sign-in of a number turns its unexpired pending grants into verified roles, in
    the same transaction, audit-logged as granted by the admin who gave them (spec §3.6)."""
    await _lock_phone(session, user.phone_e164)
    pending = (
        (
            await session.execute(
                select(PendingRoleGrant)
                .where(
                    PendingRoleGrant.phone_e164 == user.phone_e164,
                    PendingRoleGrant.expires_at > func.now(),
                )
                .order_by(PendingRoleGrant.created_at, PendingRoleGrant.id)
            )
        )
        .scalars()
        .all()
    )
    for waiting in pending:
        session.add(
            UserRole(
                user_id=user.id,
                role=waiting.role,
                scope_type=waiting.scope_type,
                scope_id=waiting.scope_id,
                status="verified",
                verified_at=datetime.now(UTC),
            )
        )
        await session.delete(waiting)
        await session.flush()
        await audit.record(
            session,
            actor_user_id=waiting.granted_by,
            action="roles.granted",
            entity_type="user",
            entity_id=str(user.id),
            after={
                "role": waiting.role,
                "scope_type": waiting.scope_type,
                "scope_id": str(waiting.scope_id) if waiting.scope_id else None,
                "pending_grant_id": str(waiting.id),
            },
        )


async def active_grants(session: AsyncSession, user_id: uuid.UUID) -> tuple[str, ...]:
    roles = await session.execute(
        select(UserRole)
        .where(UserRole.user_id == user_id, UserRole.status == "verified")
        .order_by(UserRole.created_at, UserRole.id)
    )
    return tuple(role.grant for role in roles.scalars())


async def _issue(
    session: AsyncSession,
    settings: Settings,
    *,
    user: User,
    login: LoginSession,
    parent_id: uuid.UUID | None,
    now: datetime,
) -> IssuedTokens:
    refresh_token = secrets.token_urlsafe(32)
    session.add(
        RefreshToken(
            session_id=login.id,
            token_hash=hash_refresh_token(settings, refresh_token),
            parent_id=parent_id,
            created_at=now,
        )
    )
    await session.flush()
    access_token, expires_in = issue_access_token(
        settings,
        user_id=user.id,
        session_id=login.id,
        roles=await active_grants(session, user.id),
        now=now,
    )
    return IssuedTokens(access_token, expires_in, refresh_token, login.id, login.expires_at, user)


async def start_session(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    platform: str,
    push_token: str | None = None,
    auth_time: datetime | None = None,
    now: datetime | None = None,
) -> IssuedTokens:
    """Sign a device in: a new session with its first refresh token and an access token.
    `auth_time` is when the OTP was entered (default: now, as for the dev login)."""
    at = _now(now)
    login = LoginSession(
        user_id=user.id,
        platform=platform,
        push_token=push_token,
        created_at=at,
        auth_time=auth_time or at,
        last_seen_at=at,
        expires_at=at + SESSION_LIFETIME,
    )
    session.add(login)
    await session.flush()
    if push_token is not None:
        await _release_token(session, user.id, push_token, keep=login.id)
    return await _issue(session, settings, user=user, login=login, parent_id=None, now=at)


async def _release_token(
    session: AsyncSession, user_id: uuid.UUID, token: str, *, keep: uuid.UUID
) -> None:
    """A phone has one FCM token: after a reinstall it belongs to the new session only."""
    await session.execute(
        update(LoginSession)
        .where(
            LoginSession.user_id == user_id,
            LoginSession.push_token == token,
            LoginSession.id != keep,
        )
        .values(push_token=None)
    )


async def set_push_token(
    session: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, token: str | None
) -> None:
    """Set or clear the FCM token of the calling device (M11 §3.1)."""
    await session.execute(
        update(LoginSession)
        .where(LoginSession.id == session_id, LoginSession.user_id == user_id)
        .values(push_token=token)
    )
    if token is not None:
        await _release_token(session, user_id, token, keep=session_id)


async def rotate(
    session: AsyncSession, settings: Settings, presented: str, *, now: datetime | None = None
) -> IssuedTokens:
    """Exchange a refresh token for a new one (and a new access token).

    On detected reuse the session is revoked and committed *before* the 401 is raised, so the
    revocation survives the request's rollback.
    """
    at = _now(now)
    token = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == hash_refresh_token(settings, presented)
            )
        )
    ).scalar_one_or_none()
    if token is None:
        raise invalid_token()

    # Serialise every refresh of one session: two tabs refreshing at once take turns.
    login = (
        await session.execute(
            select(LoginSession).where(LoginSession.id == token.session_id).with_for_update()
        )
    ).scalar_one()
    await session.refresh(token)
    user = await session.get(User, login.user_id)
    if user is None or user.status != "active":
        raise invalid_token()
    if login.revoked_at is not None or login.expires_at <= at:
        raise invalid_token()

    if token.used_at is None and token.revoked_at is None:
        token.used_at = at
        login.last_seen_at = at
        return await _issue(session, settings, user=user, login=login, parent_id=token.id, now=at)

    if token.used_at is None:
        # Revoked without being used: superseded by the grace rule, or signed out. Not theft.
        raise invalid_token()

    child = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.parent_id == token.id)
            .order_by(RefreshToken.created_at.desc(), RefreshToken.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    within_grace = at - token.used_at <= REUSE_GRACE
    if within_grace and child is not None and child.used_at is None and child.revoked_at is None:
        child.revoked_at = at
        login.last_seen_at = at
        return await _issue(session, settings, user=user, login=login, parent_id=token.id, now=at)

    login.revoked_at = at
    login.revoke_reason = "refresh_reused"
    session_id = login.id
    await session.commit()
    raise RefreshReused(session_id)


async def revoke_session(
    session: AsyncSession,
    session_id: uuid.UUID,
    *,
    reason: str = "logout",
    now: datetime | None = None,
) -> None:
    await session.execute(
        update(LoginSession)
        .where(LoginSession.id == session_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=_now(now), revoke_reason=reason)
    )


async def revoke_all_sessions(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    reason: str = "logout_all",
    now: datetime | None = None,
) -> list[uuid.UUID]:
    """Revoke every session of the user; returns the ids that were still active."""
    result = await session.execute(
        update(LoginSession)
        .where(LoginSession.user_id == user_id, LoginSession.revoked_at.is_(None))
        .values(revoked_at=_now(now), revoke_reason=reason)
        .returning(LoginSession.id)
    )
    return list(result.scalars())


async def list_devices(
    session: AsyncSession, user_id: uuid.UUID, *, now: datetime | None = None
) -> list[LoginSession]:
    """The user's signed-in devices: sessions that are neither revoked nor expired."""
    result = await session.execute(
        select(LoginSession)
        .where(
            LoginSession.user_id == user_id,
            LoginSession.revoked_at.is_(None),
            LoginSession.expires_at > _now(now),
        )
        .order_by(LoginSession.last_seen_at.desc())
    )
    return list(result.scalars())


async def sign_out_device(session: AsyncSession, user_id: uuid.UUID, device_id: uuid.UUID) -> bool:
    """Revoke one of the user's own sessions; False if it is not theirs or already gone."""
    result = await session.execute(
        update(LoginSession)
        .where(
            LoginSession.id == device_id,
            LoginSession.user_id == user_id,
            LoginSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(UTC), revoke_reason="signed_out_by_user")
        .returning(LoginSession.id)
    )
    return result.first() is not None


async def grant_role(
    session: AsyncSession,
    *,
    phone_e164: str,
    role: str,
    scope_id: uuid.UUID | None,
    granted_by: uuid.UUID | None,
) -> UserRole:
    """Give an existing user a staff role, e.g. hub manager of one hub (audit-logged).

    `granted_by` is None only for the operator command that creates the first admin.
    """
    granted = await _grant(
        session,
        phone_e164=phone_e164,
        role=role,
        scope_id=scope_id,
        granted_by=granted_by,
        wait_for_sign_in=False,
    )
    assert isinstance(granted, UserRole)
    return granted


async def ensure_still_admin(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Before an admin changes anyone's roles (grant, revoke, a pending grant given or
    cancelled): the database, not only the 15-minute token, must say they are still a verified
    admin (owner's decision, 2026-10-26). `FOR SHARE` keeps their admin row from being deleted
    until this transaction ends, so a revoke of them waits for it, or it waits and sees the
    revoke."""
    held = (
        await session.execute(
            select(UserRole.id)
            .where(
                UserRole.user_id == user_id,
                UserRole.role == "admin",
                UserRole.status == "verified",
            )
            .with_for_update(read=True)
        )
    ).first()
    if held is None:
        raise not_admin()


def not_admin() -> ProblemError:
    return ProblemError(
        403,
        "identity.not_admin",
        "Your admin role has been removed, so you cannot change roles. Sign in again.",
    )


async def grant_role_or_wait(
    session: AsyncSession,
    *,
    phone_e164: str,
    role: str,
    scope_id: uuid.UUID | None,
    granted_by: uuid.UUID,
) -> UserRole | PendingRoleGrant:
    """`grant_role`, except that a number with no account gets a pending grant when the role is a
    staff role other than admin (spec §3.6); admin and the partner roles still need the account.
    The granting admin must still be one in the database, not only in their token."""
    await ensure_still_admin(session, granted_by)
    return await _grant(
        session,
        phone_e164=phone_e164,
        role=role,
        scope_id=scope_id,
        granted_by=granted_by,
        wait_for_sign_in=True,
    )


async def _grant(
    session: AsyncSession,
    *,
    phone_e164: str,
    role: str,
    scope_id: uuid.UUID | None,
    granted_by: uuid.UUID | None,
    wait_for_sign_in: bool,
) -> UserRole | PendingRoleGrant:
    scope_type = ROLE_SCOPE[role]
    if (scope_type is None) != (scope_id is None):
        detail = (
            f"A {role} role needs the id of its {scope_type}."
            if scope_type
            else f"A {role} role is not limited to one place; leave scope_id out."
        )
        raise ProblemError(422, "identity.invalid_scope", detail)

    await _lock_phone(session, phone_e164)
    user = (
        await session.execute(select(User).where(User.phone_e164 == phone_e164))
    ).scalar_one_or_none()
    if user is None:
        if not wait_for_sign_in or role not in PENDING_ROLES:
            raise ProblemError(
                404,
                "identity.user_not_found",
                "No account with this number; they must sign in once.",
            )
        return await _grant_pending(
            session,
            phone_e164=phone_e164,
            role=role,
            scope_type=scope_type,
            scope_id=scope_id,
            granted_by=granted_by,
        )

    existing = (
        await session.execute(
            select(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role == role,
                UserRole.scope_id.is_(None) if scope_id is None else UserRole.scope_id == scope_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None and existing.status == "verified":
        raise ProblemError(409, "identity.role_exists", "This user already has this role.")

    before = {"status": existing.status} if existing is not None else None
    grant = existing or UserRole(
        user_id=user.id, role=role, scope_type=scope_type, scope_id=scope_id
    )
    grant.status = "verified"
    grant.verified_at = datetime.now(UTC)
    session.add(grant)
    await session.flush()
    await audit.record(
        session,
        actor_user_id=granted_by,
        action="roles.granted",
        entity_type="user",
        entity_id=str(user.id),
        before=before,
        after={
            "role": role,
            "scope_type": scope_type,
            "scope_id": str(scope_id) if scope_id else None,
        },
    )
    return grant


async def _grant_pending(
    session: AsyncSession,
    *,
    phone_e164: str,
    role: str,
    scope_type: str | None,
    scope_id: uuid.UUID | None,
    granted_by: uuid.UUID | None,
) -> PendingRoleGrant:
    # An expired one still waits for the nightly job; it must not block a new grant.
    await session.execute(
        delete(PendingRoleGrant).where(
            PendingRoleGrant.phone_e164 == phone_e164, PendingRoleGrant.expires_at <= func.now()
        )
    )
    created = (
        await session.execute(
            insert(PendingRoleGrant)
            .values(
                phone_e164=phone_e164,
                role=role,
                scope_type=scope_type,
                scope_id=scope_id,
                granted_by=granted_by,
                expires_at=func.now() + PENDING_GRANT_LIFETIME,
            )
            .on_conflict_do_nothing()
            .returning(PendingRoleGrant.id)
        )
    ).scalar_one_or_none()
    if created is None:
        raise ProblemError(
            409, "identity.role_exists", "This number already has this role waiting."
        )
    pending = await session.get(PendingRoleGrant, created, populate_existing=True)
    assert pending is not None
    await audit.record(
        session,
        actor_user_id=granted_by,
        action="roles.granted",
        entity_type="pending_role_grant",
        entity_id=str(pending.id),
        after={
            "role": role,
            "scope_type": scope_type,
            "scope_id": str(scope_id) if scope_id else None,
            "pending": True,
            "expires_at": pending.expires_at.isoformat(),
        },
    )
    return pending


async def grant_system_role(
    session: AsyncSession, user_id: uuid.UUID, role: str, scope_id: uuid.UUID | None
) -> None:
    """Grant a role that follows from another module's event (the captain role from M9's
    appointment): no person grants it, so the audit entry has no actor. Granting a verified role
    again changes nothing; a user who no longer exists gets nothing."""
    if await session.get(User, user_id) is None:
        return
    existing = await _role(session, user_id, role, scope_id)
    if existing is not None and existing.status == "verified":
        return
    grant = existing or UserRole(
        user_id=user_id, role=role, scope_type=ROLE_SCOPE[role], scope_id=scope_id
    )
    before = {"status": existing.status} if existing is not None else None
    grant.status = "verified"
    grant.verified_at = datetime.now(UTC)
    session.add(grant)
    await session.flush()
    await audit.record(
        session,
        actor_user_id=None,
        action="roles.granted",
        entity_type="user",
        entity_id=str(user_id),
        before=before,
        after={"role": role, "scope_id": str(scope_id) if scope_id else None},
    )


async def revoke_system_role(
    session: AsyncSession, user_id: uuid.UUID, role: str, scope_id: uuid.UUID | None
) -> None:
    """Remove a role granted by `grant_system_role` (the row is deleted: user_roles has no revoked
    status; the audit log keeps the history). Nothing to remove changes nothing."""
    existing = await _role(session, user_id, role, scope_id)
    if existing is None:
        return
    before = {
        "role": role,
        "scope_id": str(scope_id) if scope_id else None,
        "status": existing.status,
    }
    await session.delete(existing)
    await session.flush()
    await audit.record(
        session,
        actor_user_id=None,
        action="roles.revoked",
        entity_type="user",
        entity_id=str(user_id),
        before=before,
    )


async def _role(
    session: AsyncSession, user_id: uuid.UUID, role: str, scope_id: uuid.UUID | None
) -> UserRole | None:
    return (
        await session.execute(
            select(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role == role,
                UserRole.scope_id.is_(None) if scope_id is None else UserRole.scope_id == scope_id,
            )
        )
    ).scalar_one_or_none()
