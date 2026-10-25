"""Kindness point rates and donation drives (M9 §3.4).

A rate says how many points a kg or a piece of one category earns; an admin changes it, and the
change applies to donations verified from then on. A drive multiplies some categories for a few
days, for the whole platform, one hub or one society. Its `state` is computed from its IST dates,
except `cancelled`, so no job has to start or end a drive.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, and_, false, or_, select, text, true, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.kindness_schemas import DriveIn, DrivePatch, PointRateIn
from gharbasket.modules.community.models import DonationDrive, Ngo, PointRate, Society
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor
from gharbasket.platform.permissions import Permit, forbidden

IST = ZoneInfo("Asia/Kolkata")
OPEN_STATES = ("running", "upcoming")


def today() -> date:
    return datetime.now(IST).date()


def item_code_unknown(codes: list[str]) -> ProblemError:
    return ProblemError(
        422, "community.item_code_unknown", "Unknown item code.", extra={"codes": codes}
    )


def drive_not_found() -> ProblemError:
    return ProblemError(404, "community.drive_not_found", "No such drive.")


def _dates_invalid(detail: str) -> ProblemError:
    return ProblemError(422, "community.drive_dates_invalid", detail)


# --- point rates --------------------------------------------------------------------------------


async def rates(session: AsyncSession) -> list[PointRate]:
    rows = await session.execute(select(PointRate).where(PointRate.active).order_by(PointRate.code))
    return list(rows.scalars())


async def rate_map(session: AsyncSession) -> dict[str, PointRate]:
    return {rate.code: rate for rate in await rates(session)}


async def check_codes(session: AsyncSession, codes: list[str]) -> dict[str, PointRate]:
    known = await rate_map(session)
    unknown = [code for code in codes if code not in known]
    if unknown:
        raise item_code_unknown(unknown)
    return known


async def update_rate(
    session: AsyncSession, code: str, body: PointRateIn, by: uuid.UUID
) -> PointRate:
    rate = await session.get(PointRate, code, with_for_update=True)
    if rate is None or not rate.active:
        raise ProblemError(404, "community.item_code_unknown", "Unknown item code.")
    if rate.unit == "each" and body.max_qty != body.max_qty.to_integral_value():
        raise ProblemError(422, "request.invalid", "Pieces are counted whole: max_qty too.")
    before = {"points": rate.points, "max_qty": str(rate.max_qty), "label": rate.label}
    rate.points, rate.max_qty, rate.label = body.points, body.max_qty, body.label.model_dump()
    rate.updated_by = by
    await session.flush()
    after = {"points": rate.points, "max_qty": str(rate.max_qty), "label": rate.label}
    await audit.record(
        session,
        actor_user_id=by,
        action="community.point_rate_updated",
        entity_type="point_rate",
        entity_id=code,
        before=before,
        after=after,
    )
    return rate


# --- drives -------------------------------------------------------------------------------------


def drive_state(drive: DonationDrive, on: date) -> str:
    if drive.cancelled_at is not None:
        return "cancelled"
    if on < drive.starts_on:
        return "upcoming"
    return "ended" if on > drive.ends_on else "running"


def _state_is(state: str, on: date) -> ColumnElement[bool]:
    if state == "cancelled":
        return DonationDrive.cancelled_at.is_not(None)
    live = DonationDrive.cancelled_at.is_(None)
    if state == "upcoming":
        return and_(live, DonationDrive.starts_on > on)
    if state == "ended":
        return and_(live, DonationDrive.ends_on < on)
    return and_(live, DonationDrive.starts_on <= on, DonationDrive.ends_on >= on)


@dataclass(frozen=True)
class Viewer:
    """Whose drives a caller sees: hub staff (every hub, or theirs, plus platform drives), or a
    customer (the platform's, her hub's and her society's)."""

    staff: bool
    all_hubs: bool = False
    hub_ids: frozenset[uuid.UUID] = field(default_factory=frozenset[uuid.UUID])
    hub_id: uuid.UUID | None = None
    society_id: uuid.UUID | None = None

    def sees(self) -> ColumnElement[bool]:
        if self.all_hubs:
            return true()
        platform = DonationDrive.scope == "platform"
        if self.staff:
            return or_(platform, DonationDrive.hub_id.in_(self.hub_ids))
        hub = (
            and_(DonationDrive.scope == "hub", DonationDrive.hub_id == self.hub_id)
            if self.hub_id is not None
            else false()
        )
        society = (
            DonationDrive.society_id == self.society_id if self.society_id is not None else false()
        )
        return or_(platform, hub, society)


def covers(drive: DonationDrive, hub_id: uuid.UUID | None, society_id: uuid.UUID) -> bool:
    """Whether a household of this hub and society may donate in the drive."""
    if drive.scope == "society":
        return drive.society_id == society_id
    return drive.scope == "platform" or drive.hub_id == hub_id


async def list_drives(
    session: AsyncSession,
    viewer: Viewer,
    states: tuple[str, ...],
    *,
    cursor: str | None,
    limit: int,
) -> tuple[list[DonationDrive], str | None]:
    """Soonest start first; keyset-paginated on (starts_on, id)."""
    on = today()
    stmt = select(DonationDrive).where(viewer.sees(), or_(*(_state_is(s, on) for s in states)))
    if cursor is not None:
        position = decode_cursor(cursor)
        try:
            after = (date.fromisoformat(position["s"]), uuid.UUID(position["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(tuple_(DonationDrive.starts_on, DonationDrive.id) > after)
    rows = await session.execute(
        stmt.order_by(DonationDrive.starts_on, DonationDrive.id).limit(limit + 1)
    )
    found = list(rows.scalars())
    if len(found) <= limit:
        return found, None
    last = found[limit - 1]
    return found[:limit], encode_cursor({"s": last.starts_on.isoformat(), "id": str(last.id)})


K_MIN = 5  # households; fewer and a customer sees no progress numbers (k = 5, §3.7, §11)
_PROGRESS = text(
    "SELECT d.drive_id, count(DISTINCT d.household_id),"
    " coalesce(sum((l->>'qty')::numeric)"
    "   FILTER (WHERE d.status IN ('scheduled', 'picked_up')), 0),"
    " coalesce(sum((l->>'counted')::numeric)"
    "   FILTER (WHERE d.status IN ('verified', 'handed_over')), 0)"
    " FROM community.donations d CROSS JOIN LATERAL jsonb_array_elements(d.items) l"
    " WHERE d.drive_id = ANY(:ids) AND d.status NOT IN ('cancelled', 'rejected')"
    " GROUP BY d.drive_id"
)


async def progress(
    session: AsyncSession, drive_ids: list[uuid.UUID], *, exact: bool
) -> dict[uuid.UUID, tuple[int | None, int | None]]:
    """(pledged, verified) items per drive, a kilogram counting as one item; (None, None) under
    k households unless `exact` (hub staff)."""
    if not drive_ids:
        return {}
    found: dict[uuid.UUID, tuple[int | None, int | None]] = {}
    for drive_id, households, pledged, verified in await session.execute(
        _PROGRESS, {"ids": drive_ids}
    ):
        shown = exact or households >= K_MIN
        found[drive_id] = (half_up(pledged), half_up(verified)) if shown else (None, None)
    return {i: found.get(i, (0, 0) if exact else (None, None)) for i in drive_ids}


def half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def is_admin(permit: Permit) -> bool:
    """drives:write and donations:verify are held unscoped only by admins."""
    return any(grant.scope_id is None for grant in permit.grants)


async def staff_drive(session: AsyncSession, permit: Permit, drive_id: uuid.UUID) -> DonationDrive:
    """A drive the caller may change, locked: 404 outside their hubs, 403 for a platform drive
    unless admin."""
    drive = await session.get(DonationDrive, drive_id, with_for_update=True)
    if drive is None or (drive.hub_id is not None and not permit.allows(drive.hub_id)):
        raise drive_not_found()
    if drive.scope == "platform" and not is_admin(permit):
        raise forbidden("Only an admin can change a platform drive.")
    return drive


async def check_ngo(session: AsyncSession, ngo_id: uuid.UUID, hub_id: uuid.UUID | None) -> None:
    """A drive's NGO takes items, at the drive's hub when it has one (§3.5)."""
    ngo = await session.get(Ngo, ngo_id)
    if ngo is None:
        raise ProblemError(404, "community.ngo_not_found", "No such NGO.")
    if "items" not in ngo.accepts or (hub_id is not None and hub_id not in ngo.hub_ids):
        raise ProblemError(
            422, "community.ngo_does_not_accept", "This NGO does not take items at this hub."
        )


async def create_drive(session: AsyncSession, body: DriveIn, permit: Permit) -> DonationDrive:
    hub_id = body.hub_id
    if body.scope == "platform" and not is_admin(permit):
        raise forbidden("Only an admin can run a platform drive.")
    if hub_id is not None:
        if not permit.allows(hub_id):
            raise forbidden("This drive is for another hub.")
        if await hub_ops_api.get_hub(session, hub_id) is None:
            raise ProblemError(422, "request.invalid", "No such hub.")
    if body.society_id is not None:
        society = await session.get(Society, body.society_id)
        if society is None or society.hub_id is None:
            raise ProblemError(404, "community.society_not_found", "No such society.")
        if not permit.allows(society.hub_id):
            raise forbidden("This society belongs to another hub.")
        hub_id = society.hub_id
    if body.ngo_id is not None:
        await check_ngo(session, body.ngo_id, hub_id)
    await check_codes(session, body.codes)
    if body.starts_on < today():
        raise _dates_invalid("A drive cannot start in the past.")
    if body.ends_on < body.starts_on:
        raise _dates_invalid("ends_on is before starts_on.")
    drive = DonationDrive(
        **body.model_dump(include={"code", "scope", "society_id", "codes", "multiplier"}),
        name=body.name.model_dump(),
        description=None if body.description is None else body.description.model_dump(),
        hub_id=hub_id,
        starts_on=body.starts_on,
        ends_on=body.ends_on,
        goal_items=body.goal_items,
        ngo_id=body.ngo_id,
        created_by=permit.principal.user_id,
    )
    try:
        async with session.begin_nested():
            session.add(drive)
            await session.flush()
    except IntegrityError as exc:
        raise ProblemError(
            409, "community.drive_code_taken", "Another drive already has this code."
        ) from exc
    await session.refresh(drive)
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="community.drive_created",
        entity_type="donation_drive",
        entity_id=str(drive.id),
        after=body.model_dump(mode="json"),
    )
    return drive


async def patch_drive(
    session: AsyncSession, drive: DonationDrive, body: DrivePatch, by: uuid.UUID
) -> list[str]:
    """Apply a change; the names of the fields that changed. A running drive may only be
    extended, an ended or cancelled one keeps its dates, and a new start is never in the past."""
    on = today()
    state = drive_state(drive, on)
    sent: dict[str, Any] = {name: getattr(body, name) for name in body.model_fields_set}
    if "codes" in sent:
        await check_codes(session, sent["codes"])
    if sent.get("ngo_id") is not None:
        await check_ngo(session, sent["ngo_id"], drive.hub_id)
    starts, ends = sent.get("starts_on", drive.starts_on), sent.get("ends_on", drive.ends_on)
    if (starts, ends) != (drive.starts_on, drive.ends_on):
        if state in ("ended", "cancelled"):
            raise _dates_invalid(f"An {state} drive keeps its dates.")
        if state == "running" and (starts != drive.starts_on or ends < drive.ends_on):
            raise _dates_invalid("A running drive can only be extended.")
        if state == "upcoming" and starts < on:
            raise _dates_invalid("A drive cannot start in the past.")
        if ends < starts:
            raise _dates_invalid("ends_on is before starts_on.")
    before = {name: _plain(getattr(drive, name)) for name in sent if name != "state"}
    changed: list[str] = []
    for name, value in sent.items():
        if name == "state":
            if state in OPEN_STATES:
                drive.cancelled_at = datetime.now(UTC)
                changed.append("state")
            continue
        value = value.model_dump() if hasattr(value, "model_dump") else value
        if getattr(drive, name) != value:
            setattr(drive, name, value)
            changed.append(name)
    if changed:
        drive.version += 1
        await session.flush()
        await audit.record(
            session,
            actor_user_id=by,
            action="community.drive_updated",
            entity_type="donation_drive",
            entity_id=str(drive.id),
            before={name: before.get(name, state) for name in changed},
            after={name: _plain(getattr(drive, name, "cancelled")) for name in changed},
        )
    return changed


def _plain(value: Any) -> Any:
    return value if value is None or isinstance(value, (str, int, dict, list)) else str(value)
