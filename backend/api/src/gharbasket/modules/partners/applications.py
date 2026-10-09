"""Applications to become a partner (spec §3.1): apply, a hub manager's application for a rider or
packer without a household, withdraw, and the hub's review queue.

A customer applies from her household: a captain for the society she lives in, anyone else for
the household's hub. One open application per person (`applied`, `kyc_pending`, `training`); a
person who is already a partner of a kind cannot apply for it again.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import events, training
from gharbasket.modules.partners.models import (
    KINDS,
    OPEN_STATUSES,
    REQUIRED_DOCUMENTS,
    REQUIRED_TRAININGS,
    Application,
    Partner,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

# A hub manager creates applications only for these kinds (owner, 2026-10-09).
HUB_CREATED_KINDS = ("rider", "packer")


def not_found() -> ProblemError:
    return ProblemError(404, "partners.application_not_found", "No such application.")


def _kind_invalid() -> ProblemError:
    return ProblemError(422, "partners.kind_invalid", "Choose kitchen, captain, rider or packer.")


def display_name_or_none(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


def _check_display_name(value: str | None) -> str:
    if value is None or not 2 <= len(value) <= 40:
        raise ProblemError(
            422,
            "partners.display_name_invalid",
            "The name customers see must be 2 to 40 characters.",
        )
    return value


async def get(session: AsyncSession, application_id: uuid.UUID) -> Application:
    application = await session.get(Application, application_id)
    if application is None:
        raise not_found()
    return application


async def open_for(session: AsyncSession, user_id: uuid.UUID) -> Application | None:
    return (
        await session.execute(
            select(Application).where(
                Application.user_id == user_id, Application.status.in_(OPEN_STATUSES)
            )
        )
    ).scalar_one_or_none()


async def current_for(session: AsyncSession, user_id: uuid.UUID) -> Application | None:
    """The open application, else the latest one."""
    found = await open_for(session, user_id)
    if found is not None:
        return found
    return (
        await session.execute(
            select(Application)
            .where(Application.user_id == user_id)
            .order_by(Application.created_at.desc(), Application.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def for_user(session: AsyncSession, user_id: uuid.UUID) -> list[Application]:
    rows = await session.execute(
        select(Application).where(Application.user_id == user_id).order_by(Application.created_at)
    )
    return list(rows.scalars())


def _already_applied(**ref: uuid.UUID) -> ProblemError:
    return ProblemError(
        409,
        "partners.already_applied",
        "You already have an open application, or are already this kind of partner.",
        extra={name: str(value) for name, value in ref.items()},
    )


async def _ensure_can_apply(session: AsyncSession, user_id: uuid.UUID, kind: str) -> None:
    found = await open_for(session, user_id)
    if found is not None:
        raise _already_applied(application_id=found.id)
    partner = (
        await session.execute(
            select(Partner.id).where(
                Partner.user_id == user_id, Partner.kind == kind, Partner.status != "left"
            )
        )
    ).scalar_one_or_none()
    if partner is not None:
        raise _already_applied(partner_id=partner)


async def _submit(session: AsyncSession, application: Application) -> Application:
    """Insert and publish `partners.application_submitted`. Two applications at the same moment
    meet the one-open-application index: the second gets 409."""
    try:
        async with session.begin_nested():
            session.add(application)
            await session.flush()
    except IntegrityError as exc:
        found = await open_for(session, application.user_id)
        if found is None:
            raise
        raise _already_applied(application_id=found.id) from exc
    await publish(
        session,
        events.APPLICATION_SUBMITTED,
        {
            "application_id": str(application.id),
            "user_id": str(application.user_id),
            "kind": application.kind,
            "hub_id": str(application.hub_id),
            "scope_type": application.scope_type,
            "scope_id": str(application.scope_id),
        },
    )
    return application


async def apply(
    session: AsyncSession,
    user_id: uuid.UUID,
    *,
    kind: str,
    scope_id: uuid.UUID | None,
    display_name: str | None,
) -> Application:
    """A customer's application, scoped from her household (§3.1)."""
    if kind not in KINDS:
        raise _kind_invalid()
    await _ensure_can_apply(session, user_id, kind)
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        raise ProblemError(
            422, "partners.household_required", "Set up your household before you apply."
        )
    if household.hub_id is None:
        raise ProblemError(422, "partners.no_hub", "Your society is not served by a hub yet.")
    if kind == "captain":
        # Captains live in the society they serve (M9 §3.1); no proof in the prototype.
        scope_type, own_scope = "society", household.society_id
        if scope_id is not None and scope_id != own_scope:
            raise ProblemError(
                422,
                "partners.captain_not_resident",
                "A captain serves the society she lives in.",
            )
    else:
        scope_type, own_scope = "hub", household.hub_id
        if scope_id is not None and scope_id != own_scope:
            raise ProblemError(
                422, "partners.scope_invalid", "You can apply only at your household's hub."
            )
    name = display_name_or_none(display_name)
    if name is None:
        contact = (await identity_api.user_contacts(session, [user_id])).get(user_id)
        name = display_name_or_none(contact.name if contact else None)
    application = Application(
        user_id=user_id,
        kind=kind,
        scope_type=scope_type,
        scope_id=own_scope,
        hub_id=household.hub_id,
        display_name=_check_display_name(name),
        status="applied",
        version=1,
    )
    return await _submit(session, application)


async def create_for(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID,
    kind: str,
    phone_e164: str,
    display_name: str,
    by_user_id: uuid.UUID,
) -> Application:
    """A rider or packer application a hub manager creates for a signed-up person without a
    household (owner, 2026-10-09). Audit-logged."""
    if kind not in HUB_CREATED_KINDS:
        raise _kind_invalid()
    name = _check_display_name(display_name_or_none(display_name))
    user_id = await identity_api.user_by_phone(session, phone_e164)
    if user_id is None:
        raise ProblemError(
            422,
            "partners.user_not_found",
            "Nobody has signed up with this number; ask them to sign in to the app once.",
        )
    await _ensure_can_apply(session, user_id, kind)
    application = Application(
        user_id=user_id,
        kind=kind,
        scope_type="hub",
        scope_id=hub_id,
        hub_id=hub_id,
        display_name=name,
        created_by=by_user_id,
        status="applied",
        version=1,
    )
    await _submit(session, application)
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.application_created",
        entity_type="partner_application",
        entity_id=str(application.id),
        after={"user_id": str(user_id), "kind": kind, "hub_id": str(hub_id)},
    )
    return application


def ensure_open(application: Application) -> None:
    if application.status not in OPEN_STATUSES:
        raise ProblemError(409, "partners.application_closed", "This application is closed.")


async def withdraw(session: AsyncSession, application: Application) -> Application:
    """The applicant ends her application at any time before verification."""
    ensure_open(application)
    application.status = "withdrawn"
    application.version += 1
    await session.flush()
    return application


@dataclass(frozen=True)
class View:
    """What an Application shows besides its columns (§3.1)."""

    documents: list[dict[str, Any]]
    trainings: list[dict[str, Any]]
    next_step: str | None


def next_step(application: Application, documents: list[dict[str, Any]]) -> str | None:
    if application.status == "applied":
        return "documents"
    if application.status == "kyc_pending":
        redo = any(doc["status"] in ("missing", "rejected") for doc in documents)
        return "documents" if redo else "review"
    if application.status == "training":
        return "training"
    return None


async def views(session: AsyncSession, applications: list[Application]) -> dict[uuid.UUID, View]:
    """Requirements and next step of each application. Documents arrive with the KYC PR: until
    then every required type is missing."""
    states = await training.states_for_users(session, [a.user_id for a in applications])
    out: dict[uuid.UUID, View] = {}
    for application in applications:
        documents: list[dict[str, Any]] = [
            {"doc_type": doc_type, "status": "missing", "document_id": None, "reason": None}
            for doc_type in REQUIRED_DOCUMENTS[application.kind]
        ]
        mine = states.get(application.user_id, {})
        trainings = [
            {"code": code, "status": mine[code].status if code in mine else "todo"}
            for code in REQUIRED_TRAININGS[application.kind]
        ]
        out[application.id] = View(documents, trainings, next_step(application, documents))
    return out


async def queue(
    session: AsyncSession,
    hub_id: uuid.UUID,
    *,
    status: str,
    kind: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Application], str | None]:
    """The hub's applications in one status, oldest submitted first (keyset)."""
    stmt = select(Application).where(Application.hub_id == hub_id, Application.status == status)
    if kind is not None:
        stmt = stmt.where(Application.kind == kind)
    if cursor is not None:
        invalid = ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
        position = decode_cursor(cursor)
        try:
            at = datetime.fromisoformat(str(position.get("at")))
            last_id = uuid.UUID(str(position.get("id")))
        except ValueError as exc:
            raise invalid from exc
        if at.tzinfo is None:
            raise invalid
        stmt = stmt.where(tuple_(Application.created_at, Application.id) > tuple_(at, last_id))
    rows = list(
        (
            await session.execute(
                stmt.order_by(Application.created_at, Application.id).limit(limit + 1)
            )
        ).scalars()
    )
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor({"at": last.created_at.isoformat(), "id": str(last.id)})
    return rows[:limit], next_cursor


async def requested_societies(
    session: AsyncSession, application_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID]:
    """The society each captain application asked for, by application id."""
    if not application_ids:
        return {}
    rows = await session.execute(
        select(Application.id, Application.scope_id).where(
            Application.id.in_(set(application_ids)), Application.scope_type == "society"
        )
    )
    return {application_id: scope_id for application_id, scope_id in rows.all()}
