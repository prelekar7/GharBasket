"""Staff lookup, revoke and the hub's staff (spec §3.6).

The admin lookup shows personal data to staff, so every call is audit-logged (the filter kind and
the ids returned, never the typed number or name). The hub list masks phones. A revoke deletes the
`user_roles` row (there is no `revoked` status; the audit log keeps who did it); it reaches the
person's access token at the next refresh, within 15 minutes (spec §3.1).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Text, cast, delete, func, literal, or_, select, text, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity.models import (
    STAFF_ROLES,
    LoginSession,
    PendingRoleGrant,
    User,
    UserRole,
)
from gharbasket.modules.identity.schemas import (
    AdminUser,
    HubStaff,
    HubStaffSociety,
    PendingGrant,
    StaffGrant,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor

# How close a typed name must be to count as a match (pg_trgm word similarity).
NAME_MATCH_THRESHOLD = 0.4
PARTNER_ROLES = frozenset({"kitchen_partner", "captain", "rider", "packer"})

_BAD_CURSOR = ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")


def mask_phone(phone_e164: str) -> str:
    """ "+919800001011" -> "******1011": the last four digits only."""
    return "******" + phone_e164[-4:]


def _position(cursor: str, order: str) -> list[Any]:
    position = decode_cursor(cursor)
    after: Any = position.get("after")
    if position.get("order") != order or not isinstance(after, list):
        raise _BAD_CURSOR
    return after  # pyright: ignore[reportUnknownVariableType]


def _uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise _BAD_CURSOR from exc


# --- scope labels ---------------------------------------------------------------------------------


async def scope_labels(
    session: AsyncSession, scopes: set[tuple[str | None, uuid.UUID | None]]
) -> dict[tuple[str, uuid.UUID], str]:
    """ "Kharghar hub", "Sunrise Towers"; an NGO has no label until M9 ships a facade for it."""
    labels: dict[tuple[str, uuid.UUID], str] = {}
    societies = [scope_id for kind, scope_id in scopes if kind == "society" and scope_id]
    for society_id, society in (await community_api.society_labels(session, societies)).items():
        labels[("society", society_id)] = society.name
    for kind, scope_id in scopes:
        if kind == "hub" and scope_id is not None:
            hub = await hub_ops_api.get_hub(session, scope_id)
            if hub is not None:
                labels[("hub", scope_id)] = hub.name
    return labels


def _label(labels: dict[tuple[str, uuid.UUID], str], kind: str | None, scope_id: Any) -> str | None:
    return None if kind is None or scope_id is None else labels.get((kind, scope_id))


# --- the admin lookup -----------------------------------------------------------------------------


async def _admin_users(session: AsyncSession, users: list[User]) -> list[AdminUser]:
    if not users:
        return []
    ids = [user.id for user in users]
    roles = (
        (
            await session.execute(
                select(UserRole)
                .where(UserRole.user_id.in_(ids))
                .order_by(UserRole.created_at, UserRole.id)
            )
        )
        .scalars()
        .all()
    )
    seen = {
        user_id: last_seen
        for user_id, last_seen in await session.execute(
            select(LoginSession.user_id, func.max(LoginSession.last_seen_at))
            .where(LoginSession.user_id.in_(ids))
            .group_by(LoginSession.user_id)
        )
    }
    labels = await scope_labels(session, {(role.scope_type, role.scope_id) for role in roles})
    out: list[AdminUser] = []
    for user in users:
        out.append(
            AdminUser.model_validate(
                {
                    "id": user.id,
                    "name": user.name,
                    "phone_e164": user.phone_e164,
                    "locale": user.locale,
                    "status": user.status,
                    "last_seen_at": seen.get(user.id),
                    "roles": [
                        StaffGrant(
                            id=role.id,
                            role=role.role,  # pyright: ignore[reportArgumentType]
                            scope_type=role.scope_type,  # pyright: ignore[reportArgumentType]
                            scope_id=role.scope_id,
                            scope_label=_label(labels, role.scope_type, role.scope_id),
                            status=role.status,  # pyright: ignore[reportArgumentType]
                            verified_at=role.verified_at,
                        )
                        for role in roles
                        if role.user_id == user.id
                    ],
                }
            )
        )
    return out


async def _audit_lookup(
    session: AsyncSession,
    admin_id: uuid.UUID,
    kind: str,
    *,
    user_ids: list[uuid.UUID] | None = None,
    grant_ids: list[uuid.UUID] | None = None,
) -> None:
    after: dict[str, Any] = {"filter": kind}
    if user_ids is not None:
        after["user_ids"] = [str(i) for i in user_ids]
    if grant_ids is not None:
        after["pending_grant_ids"] = [str(i) for i in grant_ids]
    await audit.record(
        session,
        actor_user_id=admin_id,
        action="identity.users_looked_up",
        entity_type="user_lookup",
        entity_id=str(admin_id),
        after=after,
    )


async def users_by_phone(
    session: AsyncSession, admin_id: uuid.UUID, phone_e164: str
) -> list[AdminUser]:
    user = (
        await session.execute(select(User).where(User.phone_e164 == phone_e164))
    ).scalar_one_or_none()
    found = await _admin_users(session, [] if user is None else [user])
    await _audit_lookup(session, admin_id, "phone", user_ids=[u.id for u in found])
    return found


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def users_by_name(
    session: AsyncSession, admin_id: uuid.UUID, q: str, *, cursor: str | None, limit: int
) -> tuple[list[AdminUser], str | None]:
    """Best match first. The cursor is an offset: a name search returns few rows, and a rank is
    not a stable key."""
    offset = 0
    if cursor is not None:
        after = _position(cursor, "q")
        if len(after) != 1 or not isinstance(after[0], int) or after[0] < 0:
            raise _BAD_CURSOR
        offset = after[0]
    await session.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
        {"t": str(NAME_MATCH_THRESHOLD)},
    )
    typed = cast(literal(q), Text)
    rank = func.word_similarity(typed, User.name)
    rows = (
        (
            await session.execute(
                select(User)
                .where(
                    User.name.is_not(None),
                    or_(typed.op("<%")(User.name), User.name.ilike(f"%{_escape_like(q)}%")),
                )
                .order_by(rank.desc(), User.name, User.id)
                .offset(offset)
                .limit(limit + 1)
            )
        )
        .scalars()
        .all()
    )
    page = list(rows[:limit])
    next_cursor = (
        encode_cursor({"order": "q", "after": [offset + limit]}) if len(rows) > limit else None
    )
    found = await _admin_users(session, page)
    await _audit_lookup(session, admin_id, "q", user_ids=[u.id for u in found])
    return found, next_cursor


async def staff_users(
    session: AsyncSession, admin_id: uuid.UUID, *, cursor: str | None, limit: int
) -> tuple[list[AdminUser], str | None]:
    name_key = func.coalesce(User.name, "")
    stmt = select(User).where(
        User.id.in_(select(UserRole.user_id).where(UserRole.role.in_(STAFF_ROLES)))
    )
    if cursor is not None:
        name, last = _position(cursor, "staff")
        if not isinstance(name, str):
            raise _BAD_CURSOR
        stmt = stmt.where(tuple_(name_key, User.id) > tuple_(name, _uuid(last)))
    rows = (
        (await session.execute(stmt.order_by(name_key, User.id).limit(limit + 1))).scalars().all()
    )
    page = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit:
        tail = page[-1]
        next_cursor = encode_cursor({"order": "staff", "after": [tail.name or "", str(tail.id)]})
    found = await _admin_users(session, page)
    await _audit_lookup(session, admin_id, "staff", user_ids=[u.id for u in found])
    return found, next_cursor


def pending_view(grant: PendingRoleGrant, labels: dict[tuple[str, uuid.UUID], str]) -> PendingGrant:
    return PendingGrant.model_validate(
        {
            "id": grant.id,
            "phone_masked": mask_phone(grant.phone_e164),
            "role": grant.role,
            "scope_type": grant.scope_type,
            "scope_id": grant.scope_id,
            "scope_label": _label(labels, grant.scope_type, grant.scope_id),
            "status": "pending",
            "granted_at": grant.created_at,
            "expires_at": grant.expires_at,
        }
    )


async def pending_view_of(session: AsyncSession, grant: PendingRoleGrant) -> PendingGrant:
    labels = await scope_labels(session, {(grant.scope_type, grant.scope_id)})
    return pending_view(grant, labels)


async def pending_grants(
    session: AsyncSession, admin_id: uuid.UUID, *, cursor: str | None, limit: int
) -> tuple[list[PendingGrant], str | None]:
    """Unexpired pending grants, newest first."""
    stmt = select(PendingRoleGrant).where(PendingRoleGrant.expires_at > func.now())
    if cursor is not None:
        created, last = _position(cursor, "pending")
        try:
            created_at = datetime.fromisoformat(str(created))
        except ValueError as exc:
            raise _BAD_CURSOR from exc
        stmt = stmt.where(
            tuple_(PendingRoleGrant.created_at, PendingRoleGrant.id)
            < tuple_(created_at, _uuid(last))
        )
    rows = (
        (
            await session.execute(
                stmt.order_by(PendingRoleGrant.created_at.desc(), PendingRoleGrant.id.desc()).limit(
                    limit + 1
                )
            )
        )
        .scalars()
        .all()
    )
    page = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit:
        tail = page[-1]
        next_cursor = encode_cursor(
            {"order": "pending", "after": [tail.created_at.isoformat(), str(tail.id)]}
        )
    labels = await scope_labels(session, {(g.scope_type, g.scope_id) for g in page})
    await _audit_lookup(session, admin_id, "pending", grant_ids=[g.id for g in page])
    return [pending_view(g, labels) for g in page], next_cursor


# --- revoke ---------------------------------------------------------------------------------------


def _scope_json(scope_type: str | None, scope_id: uuid.UUID | None) -> dict[str, Any]:
    return {"scope_type": scope_type, "scope_id": str(scope_id) if scope_id else None}


async def revoke_grant(session: AsyncSession, grant_id: uuid.UUID, admin_id: uuid.UUID) -> None:
    """Delete a staff grant, or cancel a pending one; audit-logged with the admin."""
    role = (
        await session.execute(select(UserRole.role).where(UserRole.id == grant_id))
    ).scalar_one_or_none()
    admins: list[UserRole] = []
    if role == "admin":
        # Lock every verified admin row, in one order, before the target row: two admins removing
        # each other then wait for one another (and the second finds the last admin) instead of
        # each holding its target and deadlocking on the other's.
        admins = list(
            (
                await session.execute(
                    select(UserRole)
                    .where(UserRole.role == "admin", UserRole.status == "verified")
                    .order_by(UserRole.id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            )
            .scalars()
            .all()
        )
    row = (
        await session.execute(
            select(UserRole)
            .where(UserRole.id == grant_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None and role is not None:
        raise ProblemError(404, "identity.role_not_found", "No such role grant.")  # just revoked
    if row is None:
        waiting = await session.get(PendingRoleGrant, grant_id, with_for_update=True)
        if waiting is None:
            raise ProblemError(404, "identity.role_not_found", "No such role grant.")
        before = {"role": waiting.role, **_scope_json(waiting.scope_type, waiting.scope_id)}
        await session.delete(waiting)
        await session.flush()
        await audit.record(
            session,
            actor_user_id=admin_id,
            action="roles.revoked",
            entity_type="pending_role_grant",
            entity_id=str(grant_id),
            before={**before, "pending": True},
        )
        return

    if row.role not in STAFF_ROLES:
        raise ProblemError(
            409,
            "identity.role_not_removable",
            "This role cannot be removed here: suspend the partner in partner management, or "
            "end the captain's appointment."
            if row.role in PARTNER_ROLES
            else "Every account is a customer; that role stays.",
        )
    if row.role == "admin":
        if row.user_id == admin_id:
            raise ProblemError(
                409, "identity.cannot_remove_own_admin", "You cannot remove your own admin role."
            )
        if row.status == "verified" and len(admins) <= 1:
            raise ProblemError(409, "identity.last_admin", "This is the last admin.")
    before = {"role": row.role, "status": row.status, **_scope_json(row.scope_type, row.scope_id)}
    user_id = row.user_id
    await session.delete(row)
    await session.flush()
    await audit.record(
        session,
        actor_user_id=admin_id,
        action="roles.revoked",
        entity_type="user",
        entity_id=str(user_id),
        before=before,
    )


# --- the hub's staff ------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    sort: tuple[str, str, str]
    staff: HubStaff


def _hub_staff(
    *,
    grant_id: uuid.UUID,
    user_id: uuid.UUID | None,
    name: str | None,
    phone: str,
    role: str,
    society: HubStaffSociety | None,
    status: str,
    since: datetime | None,
) -> _Row:
    staff = HubStaff.model_validate(
        {
            "grant_id": grant_id,
            "user_id": user_id,
            "name": name,
            "phone_masked": mask_phone(phone),
            "role": role,
            "works_as": "partner" if role in PARTNER_ROLES else "staff",
            "society": society,
            "status": status,
            "since": since,
        }
    )
    return _Row((role, (name or "").casefold(), str(grant_id)), staff)


async def hub_staff(
    session: AsyncSession, hub_id: uuid.UUID, *, role: str | None, cursor: str | None, limit: int
) -> tuple[list[HubStaff], str | None]:
    """The grants scoped to the hub, its societies' captains and the hub's pending grants,
    by role and then name. Admins and other unscoped grants are not listed."""
    society_ids = await community_api.hub_society_ids(session, hub_id)
    scoped = or_(
        (UserRole.scope_type == "hub") & (UserRole.scope_id == hub_id),
        (UserRole.role == "captain") & UserRole.scope_id.in_(society_ids),
    )
    stmt = select(UserRole, User.name, User.phone_e164, User.id).join(
        User, User.id == UserRole.user_id
    )
    pending_stmt = select(PendingRoleGrant).where(
        PendingRoleGrant.scope_type == "hub",
        PendingRoleGrant.scope_id == hub_id,
        PendingRoleGrant.expires_at > func.now(),
    )
    if role is not None:
        stmt = stmt.where(UserRole.role == role)
        pending_stmt = pending_stmt.where(PendingRoleGrant.role == role)
    grants = (await session.execute(stmt.where(scoped))).all()
    waiting = (await session.execute(pending_stmt)).scalars().all()

    captain_societies = [g.scope_id for g, *_ in grants if g.role == "captain" and g.scope_id]
    societies = await community_api.society_labels(session, captain_societies)
    rows: list[_Row] = []
    for grant, name, phone, user_id in grants:
        society = None
        if grant.role == "captain" and grant.scope_id is not None and grant.scope_id in societies:
            society = HubStaffSociety(id=grant.scope_id, name=societies[grant.scope_id].name)
        rows.append(
            _hub_staff(
                grant_id=grant.id,
                user_id=user_id,
                name=name,
                phone=phone,
                role=grant.role,
                society=society,
                status=grant.status,
                since=grant.verified_at,
            )
        )
    for invited in waiting:
        rows.append(
            _hub_staff(
                grant_id=invited.id,
                user_id=None,
                name=None,
                phone=invited.phone_e164,
                role=invited.role,
                society=None,
                status="invited",
                since=invited.created_at,
            )
        )
    rows.sort(key=lambda r: r.sort)
    if cursor is not None:
        after = _position(cursor, "hub_staff")
        if len(after) != 3 or not all(isinstance(part, str) for part in after):  # pyright: ignore[reportUnknownArgumentType]
            raise _BAD_CURSOR
        rows = [r for r in rows if r.sort > (after[0], after[1], after[2])]
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor({"order": "hub_staff", "after": list(page[-1].sort)})
    return [r.staff for r in page], next_cursor


async def expire_pending(session: AsyncSession, at: datetime) -> int:
    """Delete pending grants past their 30 days: a phone number is personal data."""
    gone = await session.execute(
        delete(PendingRoleGrant)
        .where(PendingRoleGrant.expires_at <= at)
        .returning(PendingRoleGrant.id)
    )
    return len(gone.all())
