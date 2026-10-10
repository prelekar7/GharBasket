"""Society Captains (M9 §3.2, ADR-0026 §4): appoint, end, and the consumers of M8's events.

M8 verifies the person; M9 records which verified captain serves which society. Every appointment
publishes `community.captain_assigned` and every end `community.captain_ended`, from which M1
grants and revokes `captain:<society>`. Neither event carries a name, flat or phone.

Concurrency: appointing locks the society row, so two appointments in one society queue up and
the second sees the first; the partial unique indexes are the last guard (a partner appointed in
two societies at once), and their violation answers the same 409s.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import events
from gharbasket.modules.community.models import Captain, Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish


def society_not_found() -> ProblemError:
    return ProblemError(404, "community.society_not_found", "No such society.")


def captain_not_found() -> ProblemError:
    return ProblemError(404, "community.captain_not_found", "No such captain appointment.")


def _exists(captain: Captain) -> ProblemError:
    return ProblemError(
        409,
        "community.captain_exists",
        "This society already has a captain. Replace her, or end her appointment first.",
        extra={"captain_id": str(captain.id)},
    )


def _already_captain() -> ProblemError:
    return ProblemError(
        409, "community.already_captain", "She is already the captain of another society."
    )


async def active_for_society(session: AsyncSession, society_id: uuid.UUID) -> Captain | None:
    return (
        await session.execute(
            select(Captain).where(Captain.society_id == society_id, Captain.status == "active")
        )
    ).scalar_one_or_none()


async def active_for_partner(session: AsyncSession, partner_id: uuid.UUID) -> Captain | None:
    return (
        await session.execute(
            select(Captain).where(Captain.partner_id == partner_id, Captain.status == "active")
        )
    ).scalar_one_or_none()


async def history(
    session: AsyncSession, society_id: uuid.UUID, status: str | None
) -> list[Captain]:
    stmt = select(Captain).where(Captain.society_id == society_id)
    if status is not None:
        stmt = stmt.where(Captain.status == status)
    rows = await session.execute(stmt.order_by(Captain.since.desc(), Captain.id.desc()))
    return list(rows.scalars())


async def appoint(
    session: AsyncSession,
    society_id: uuid.UUID,
    partner_id: uuid.UUID,
    *,
    replace: bool,
    appointed_by: str,
    by_user_id: uuid.UUID | None,
) -> Captain:
    """Appoint a verified captain partner who lives in the society; in the caller's transaction.

    `replace` ends the current captain (`replaced`) first. Refusals, in order: 404
    `community.society_not_found`; 422 `community.partner_not_eligible`, then
    `community.captain_not_resident`; 409 `community.already_captain`, then
    `community.captain_exists`.
    """
    society = (
        await session.execute(select(Society).where(Society.id == society_id).with_for_update())
    ).scalar_one_or_none()
    if society is None:
        raise society_not_found()
    partner = await partners_api.get_partner(session, partner_id)
    if (
        partner is None
        or partner.kind != "captain"
        or partner.status != "active"
        or society.hub_id is None
        or partner.hub_id != society.hub_id
    ):
        raise ProblemError(
            422,
            "community.partner_not_eligible",
            "Only an active captain partner of this society's hub can be appointed.",
        )
    household = await identity_api.household_for_user(session, partner.user_id)
    if household is None or household.society_id != society.id:
        raise ProblemError(
            422,
            "community.captain_not_resident",
            "A captain must live in the society she serves.",
        )
    elsewhere = await active_for_partner(session, partner.id)
    current = await active_for_society(session, society.id)
    if elsewhere is not None and elsewhere.society_id != society.id:
        raise _already_captain()
    if current is not None:
        if not replace or current.partner_id == partner.id:
            raise _exists(current)
        await end(session, current, reason="replaced", note=None, by_user_id=by_user_id)

    captain = Captain(
        society_id=society.id,
        hub_id=society.hub_id,
        user_id=partner.user_id,
        partner_id=partner.id,
        status="active",
        since=datetime.now(UTC),
        appointed_by=appointed_by,
        appointed_by_user_id=by_user_id,
    )
    try:
        async with session.begin_nested():
            session.add(captain)
            await session.flush()
    except IntegrityError as exc:
        # Lost a race the society lock does not cover (the same partner in two societies).
        taken = await active_for_society(session, society.id)
        raise (_exists(taken) if taken is not None else _already_captain()) from exc
    await publish(
        session,
        events.CAPTAIN_ASSIGNED,
        {
            "captain_id": str(captain.id),
            "society_id": str(captain.society_id),
            "hub_id": str(captain.hub_id),
            "user_id": str(captain.user_id),
            "partner_id": str(captain.partner_id),
            "since": captain.since.isoformat(),
            "appointed_by": appointed_by,
        },
    )
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="community.captain_assigned",
        entity_type="captain",
        entity_id=str(captain.id),
        after={
            "society_id": str(captain.society_id),
            "partner_id": str(captain.partner_id),
            "appointed_by": appointed_by,
        },
    )
    return captain


async def end(
    session: AsyncSession,
    captain: Captain,
    *,
    reason: str,
    note: str | None,
    by_user_id: uuid.UUID | None,
) -> Captain:
    """End an appointment now and publish `community.captain_ended`; 409
    `community.captain_already_ended` for an ended one."""
    if captain.status != "active":
        raise ProblemError(
            409, "community.captain_already_ended", "This appointment has already ended."
        )
    until = datetime.now(UTC)
    captain.status = "ended"
    captain.until = until
    captain.end_reason = reason
    captain.ended_by_user_id = by_user_id
    captain.note = note
    await session.flush()
    await publish(
        session,
        events.CAPTAIN_ENDED,
        {
            "captain_id": str(captain.id),
            "society_id": str(captain.society_id),
            "hub_id": str(captain.hub_id),
            "user_id": str(captain.user_id),
            "partner_id": str(captain.partner_id),
            "until": until.isoformat(),
            "reason": reason,
        },
    )
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="community.captain_ended",
        entity_type="captain",
        entity_id=str(captain.id),
        before={"status": "active"},
        after={"status": "ended", "reason": reason, "note": note},
    )
    return captain


# --- consumers of M8's events (registered in jobs.py) -------------------------------------------


def _uuid(payload: dict[str, Any], key: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(payload[key]))
    except (KeyError, ValueError):
        return None


async def on_partner_verified(session: AsyncSession, event: OutboxEvent) -> None:
    """A verified captain is appointed to the society she asked for when it has no captain, she
    serves no other, and she lives there; otherwise her request waits for a hub manager. A
    duplicate delivery appoints once. Never raises on a bad payload (consumers of an event share
    one savepoint)."""
    payload = event.payload
    if payload.get("kind") != "captain":
        return
    partner_id, society_id = _uuid(payload, "partner_id"), _uuid(payload, "requested_society_id")
    if partner_id is None or society_id is None:
        return
    # Redelivered: an appointment of hers made since this event was published is this event's.
    since_event = await session.execute(
        select(Captain.id).where(
            Captain.partner_id == partner_id, Captain.created_at >= event.created_at
        )
    )
    if since_event.first() is not None:
        return
    try:
        async with session.begin_nested():
            await appoint(
                session,
                society_id,
                partner_id,
                replace=False,
                appointed_by="auto",
                by_user_id=None,
            )
    except ProblemError:
        return  # taken, elsewhere, not resident or not eligible: the hub manager decides


async def on_partner_suspended(session: AsyncSession, event: OutboxEvent) -> None:
    """A suspended partner (or one who left) stops serving her society at once."""
    partner_id = _uuid(event.payload, "partner_id")
    if partner_id is None or event.payload.get("kind", "captain") != "captain":
        return
    captain = await active_for_partner(session, partner_id)
    if captain is not None:
        await end(session, captain, reason="suspended", note=None, by_user_id=None)
