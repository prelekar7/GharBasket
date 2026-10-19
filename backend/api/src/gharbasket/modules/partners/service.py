"""Business logic of M8 Partner Network: the partner core (spec §3.3).

`verify` creates the partner record and publishes `partners.verified`; the S6 review and training
endpoints call it in the same transaction as the step that completes an application, and the seed
calls it for its captains. `suspend` and `reinstate` publish `partners.suspended` and
`partners.reinstated`, from which M1 changes the role and M9 ends a captain's appointment.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import cooking, events, kitchens
from gharbasket.modules.partners.models import Partner
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

# Pause is for kitchens, riders and packers: a captain who steps back is ended in M9 (§3.3).
PAUSABLE = ("kitchen", "rider", "packer")


def not_found() -> ProblemError:
    return ProblemError(404, "partners.partner_not_found", "No such partner.")


async def get(session: AsyncSession, partner_id: uuid.UUID, *, for_update: bool = False) -> Partner:
    partner = await session.get(Partner, partner_id, with_for_update=for_update)
    if partner is None:
        raise not_found()
    return partner


async def for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Partner]:
    rows = await session.execute(
        select(Partner).where(Partner.user_id == user_id).order_by(Partner.kind)
    )
    return list(rows.scalars())


async def verify(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    kind: str,
    hub_id: uuid.UUID,
    display_name: str,
    application_id: uuid.UUID | None = None,
    requested_society_id: uuid.UUID | None = None,
    data_source: str = "real",
) -> Partner:
    """Make the person a partner of this kind and publish `partners.verified`, in the caller's
    transaction.

    One record per (user, kind): a person who left comes back on her old record; anyone else who
    is already this kind of partner gets 409 `partners.already_applied` (with `partner_id`).
    """
    partner = (
        await session.execute(
            select(Partner)
            .where(Partner.user_id == user_id, Partner.kind == kind)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if partner is not None and partner.status != "left":
        raise ProblemError(
            409,
            "partners.already_applied",
            "This person is already this kind of partner.",
            extra={"partner_id": str(partner.id)},
        )
    now = datetime.now(UTC)
    if partner is None:
        partner = Partner(user_id=user_id, kind=kind, data_source=data_source, version=1)
        session.add(partner)
    else:
        partner.version += 1
    partner.hub_id = hub_id
    partner.application_id = application_id
    partner.display_name = display_name
    partner.status = "active"
    partner.verified_at = now
    partner.suspended_at = partner.suspend_reason_code = partner.suspend_note = None
    await session.flush()
    await publish(
        session,
        events.VERIFIED,
        {
            "partner_id": str(partner.id),
            "user_id": str(user_id),
            "kind": kind,
            "hub_id": str(hub_id),
            "requested_society_id": str(requested_society_id)
            if kind == "captain" and requested_society_id is not None
            else None,
        },
    )
    return partner


def _ref(partner: Partner) -> dict[str, Any]:
    return {
        "partner_id": str(partner.id),
        "user_id": str(partner.user_id),
        "kind": partner.kind,
        "hub_id": str(partner.hub_id),
    }


async def suspend(
    session: AsyncSession,
    partner: Partner,
    *,
    reason_code: str,
    note: str | None,
    by_user_id: uuid.UUID | None,
) -> Partner:
    """Suspend at once (audit-logged) and publish `partners.suspended`: M1 suspends the hub role
    and M9 ends a captain's appointment; a kitchen's offered and accepted assignments from today
    are cancelled (§3.3). 409 `partners.already_suspended` when she is suspended
    already or has left."""
    if partner.status in ("suspended", "left"):
        raise ProblemError(
            409,
            "partners.already_suspended",
            "This partner is already suspended."
            if partner.status == "suspended"
            else "This partner has left.",
        )
    before = {"status": partner.status}
    partner.status = "suspended"
    partner.suspended_at = datetime.now(UTC)
    partner.suspend_reason_code = reason_code
    partner.suspend_note = note
    partner.version += 1
    await session.flush()
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.suspended",
        entity_type="partner",
        entity_id=str(partner.id),
        before=before,
        after={"status": "suspended", "reason_code": reason_code, "note": note},
    )
    if partner.kind == "kitchen":  # her offered and accepted tiffins go to the next round
        await cooking.cancel_for_partner(session, partner.id, kitchens.today())
    await publish(session, events.SUSPENDED, {**_ref(partner), "reason_code": reason_code})
    return partner


async def reinstate(
    session: AsyncSession, partner: Partner, *, note: str | None, by_user_id: uuid.UUID | None
) -> Partner:
    """Make a suspended partner active again (audit-logged) and publish `partners.reinstated`.
    A captain is not re-appointed: the hub manager decides in M9."""
    if partner.status != "suspended":
        raise ProblemError(409, "partners.not_suspended", "This partner is not suspended.")
    before = {"status": "suspended", "reason_code": partner.suspend_reason_code}
    partner.status = "active"
    partner.suspended_at = partner.suspend_reason_code = partner.suspend_note = None
    partner.version += 1
    await session.flush()
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.reinstated",
        entity_type="partner",
        entity_id=str(partner.id),
        before=before,
        after={"status": "active", "note": note},
    )
    await publish(session, events.REINSTATED, _ref(partner))
    return partner


async def set_paused(session: AsyncSession, partner: Partner, *, paused: bool) -> Partner:
    """Pause or resume: no roles change and no event. Pausing a paused partner (or resuming an
    active one) changes nothing."""
    if partner.kind not in PAUSABLE:
        raise ProblemError(
            409,
            "partners.pause_not_allowed",
            "A captain cannot pause; ask your hub manager to end your appointment.",
        )
    if partner.status not in ("active", "paused"):
        raise ProblemError(
            409, "partners.pause_not_allowed", "Only an active or paused partner can do this."
        )
    target = "paused" if paused else "active"
    if partner.status != target:
        partner.status = target
        partner.version += 1
        await session.flush()
    return partner


async def leave_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """She asked to delete her account (M1 §3.5): every record of hers that has not left becomes
    `left`, her offered and accepted tiffins are cancelled as for a suspension, `partners.suspended`
    goes out with `reason_code: "left"` (M9 ends a captain's appointment, M1 the role), and her
    KYC files may be deleted 30 days later. What she earned is still paid (§3.7). Returns how
    many records left; running it again changes nothing."""
    from gharbasket.modules.partners import kyc  # deferred: kyc imports this module's siblings

    gone = [p for p in await for_user(session, user_id) if p.status != "left"]
    for partner in gone:
        before = {"status": partner.status}
        partner.status = "left"
        partner.version += 1
        await session.flush()
        await audit.record(
            session,
            actor_user_id=None,
            action="partners.left",
            entity_type="partner",
            entity_id=str(partner.id),
            before=before,
            after={"status": "left"},
        )
        if partner.kind == "kitchen":
            await cooking.cancel_for_partner(session, partner.id, kitchens.today())
        await publish(session, events.SUSPENDED, {**_ref(partner), "reason_code": "left"})
    if gone:
        await kyc.release_files(
            session, await kyc.all_for_user(session, user_id), datetime.now(UTC)
        )
    return len(gone)


async def rename(session: AsyncSession, partner: Partner, display_name: str) -> Partner:
    """Her own display name (2 to 40 characters, as on the application): the name customers see
    on her card. Changing it to what it is changes nothing."""
    name = " ".join(display_name.split())
    if not 2 <= len(name) <= 40:
        raise ProblemError(
            422,
            "partners.display_name_invalid",
            "The name customers see must be 2 to 40 characters.",
        )
    if name != partner.display_name:
        partner.display_name = name
        partner.version += 1
        await session.flush()
    return partner


async def list_for_hub(
    session: AsyncSession,
    hub_id: uuid.UUID,
    *,
    kind: str | None,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Partner], str | None]:
    """The hub's partners by display name, then id (keyset)."""
    stmt = select(Partner).where(Partner.hub_id == hub_id)
    if kind is not None:
        stmt = stmt.where(Partner.kind == kind)
    if status is not None:
        stmt = stmt.where(Partner.status == status)
    if cursor is not None:
        invalid = ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
        position = decode_cursor(cursor)
        name = position.get("name")
        try:
            last_id = uuid.UUID(str(position.get("id")))
        except ValueError as exc:
            raise invalid from exc
        if not isinstance(name, str):
            raise invalid
        stmt = stmt.where(tuple_(Partner.display_name, Partner.id) > tuple_(name, last_id))
    rows = list(
        (
            await session.execute(stmt.order_by(Partner.display_name, Partner.id).limit(limit + 1))
        ).scalars()
    )
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor({"name": last.display_name, "id": str(last.id)})
    return rows[:limit], next_cursor
