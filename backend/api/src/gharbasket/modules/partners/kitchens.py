"""Kitchens and their capacity (spec §3.4).

A kitchen row is made when a kitchen partner is verified (`create_for`), with the FSSAI number
from her accepted documents and the hygiene grade the hub gave at approval. She sets her area,
address, pin, skills and weekly default portions; hub staff re-grade her hygiene after an
inspection (audit-logged).

Capacity for a date and meal is her `set` override, else her weekday default. Overrides close at
21:00 IST the evening before (the cut-off), and lowering one never takes back portions already
offered: the assignment rounds read what is left (`capacity_left`, §3.4).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import kyc
from gharbasket.modules.partners.models import (
    LIVE,
    MEALS,
    SKILLS,
    Application,
    Assignment,
    Kitchen,
    KitchenCapacity,
    Partner,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import point

IST = ZoneInfo("Asia/Kolkata")
CUTOFF = time(21, 0)  # IST, the evening before the delivery date
MAX_PORTIONS = 100
WINDOW_DAYS = 14  # capacity is read and set from today for at most 14 days
DEFAULT_DAYS = 7
DAYS_IN_WEEK = 7


def now() -> datetime:
    return datetime.now(UTC)


def today() -> date:
    return now().astimezone(IST).date()


def not_found() -> ProblemError:
    return ProblemError(404, "partners.kitchen_not_found", "No such kitchen.")


def capacity_invalid(detail: str) -> ProblemError:
    return ProblemError(422, "partners.capacity_invalid", detail)


def zero_week() -> dict[str, list[int]]:
    return {meal: [0] * DAYS_IN_WEEK for meal in MEALS}


async def create_for(
    session: AsyncSession, partner: Partner, application: Application | None = None
) -> Kitchen:
    """The kitchen of a newly verified kitchen partner, in the caller's transaction. A partner
    who left and came back keeps her kitchen, refreshed from the new application."""
    kitchen = await session.get(Kitchen, partner.id)
    if kitchen is None:
        kitchen = Kitchen(partner_id=partner.id, default_portions=zero_week(), version=1)
        session.add(kitchen)
    elif application is not None:
        kitchen.version += 1
    if application is not None:
        documents = await kyc.for_application(session, application)
        fssai = next((d for d in documents if d.doc_type == "fssai"), None)
        if fssai is not None:
            kitchen.fssai_reg_no = fssai.fssai_reg_no
            valid_until = fssai.details.get("valid_until")
            kitchen.fssai_valid_until = date.fromisoformat(valid_until) if valid_until else None
        if application.hygiene_grade is not None:
            kitchen.hygiene_grade = application.hygiene_grade
            approved = application.docs_approved_at or now()
            kitchen.inspected_on = approved.astimezone(IST).date()
    await session.flush()
    return kitchen


async def own(
    session: AsyncSession, user_id: uuid.UUID, *, for_update: bool = False
) -> tuple[Partner, Kitchen]:
    """The caller's kitchen partner record and kitchen; 404 without one. `for_update` locks the
    kitchen row, for a check of its version followed by a write."""
    stmt = (
        select(Partner, Kitchen)
        .join(Kitchen, Kitchen.partner_id == Partner.id)
        .where(Partner.user_id == user_id, Partner.kind == "kitchen")
    )
    if for_update:
        stmt = stmt.with_for_update(of=Kitchen)
    row = (await session.execute(stmt)).first()
    if row is None:
        raise not_found()
    return row[0], row[1]


async def of_partner(session: AsyncSession, partner_id: uuid.UUID) -> tuple[Partner, Kitchen]:
    partner = await session.get(Partner, partner_id)
    kitchen = await session.get(Kitchen, partner_id)
    if partner is None or kitchen is None:
        raise not_found()
    return partner, kitchen


def _check_week(value: Any) -> dict[str, list[int]]:
    if not isinstance(value, dict) or set(value) != set(MEALS):
        raise capacity_invalid("Give lunch and dinner, Monday to Sunday.")
    week: dict[str, list[int]] = {}
    for meal in MEALS:
        days = value[meal]
        if (
            not isinstance(days, list)
            or len(days) != DAYS_IN_WEEK
            or any(type(n) is not int or not 0 <= n <= MAX_PORTIONS for n in days)
        ):
            raise capacity_invalid("Each day is 0 to 100 portions, Monday to Sunday.")
        week[meal] = list(days)
    return week


def check_skills(skills: list[str]) -> list[str]:
    unknown = sorted(set(skills) - set(SKILLS))
    if unknown:
        raise ProblemError(
            422,
            "partners.skills_invalid",
            "Choose skills from the list.",
            extra={"skills": unknown, "allowed": list(SKILLS)},
        )
    return sorted(set(skills), key=SKILLS.index)


async def pin_locked_dates(session: AsyncSession, kitchen: Kitchen) -> None:
    """A new weekly default must not change the capacity of a date that has closed: today, and
    tomorrow once 21:00 has passed. Each such date and meal without an override of its own gets
    the old default stored as one, so the kitchen keeps what she had when it closed."""
    for day in (today(), today() + timedelta(days=1)):
        if not is_locked(day):
            continue
        for meal in MEALS:
            await session.execute(
                insert(KitchenCapacity)
                .values(
                    {
                        KitchenCapacity.partner_id: kitchen.partner_id,
                        KitchenCapacity.on_date: day,
                        KitchenCapacity.meal: meal,
                        KitchenCapacity.max_portions: default_for(kitchen, day, meal),
                    }
                )
                .on_conflict_do_nothing(index_elements=["partner_id", "date", "meal"])
            )


async def edit(
    session: AsyncSession, partner: Partner, kitchen: Kitchen, changes: dict[str, Any]
) -> Kitchen:
    """Apply a PATCH (fields already shaped by the schema); the caller checked If-Match."""
    if "skills" in changes:
        kitchen.skills = check_skills(changes["skills"] or [])
    if "default_portions" in changes:
        week = _check_week(changes["default_portions"])
        await pin_locked_dates(session, kitchen)  # before the new default shows through
        kitchen.default_portions = week
    if "display_name" in changes and changes["display_name"] != partner.display_name:
        partner.display_name = changes["display_name"]
        partner.version += 1
    if "area" in changes:
        kitchen.area = changes["area"]
    if "address" in changes:
        kitchen.address = changes["address"]
    if "location" in changes:
        pin = changes["location"]
        kitchen.location = None if pin is None else point(pin["lat"], pin["lng"])
    kitchen.version += 1
    await session.flush()
    await session.refresh(kitchen)  # lat and lng follow the new location
    return kitchen


async def inspect(
    session: AsyncSession, kitchen: Kitchen, *, grade: str, note: str | None, by_user_id: uuid.UUID
) -> Kitchen:
    """Record an inspection's hygiene grade (audit-logged with the note)."""
    last = kitchen.inspected_on
    before = {"grade": kitchen.hygiene_grade, "inspected_on": last.isoformat() if last else None}
    inspected_on = today()
    kitchen.hygiene_grade, kitchen.inspected_on = grade, inspected_on
    kitchen.version += 1
    await session.flush()
    await session.refresh(kitchen)
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.kitchen_inspected",
        entity_type="kitchen",
        entity_id=str(kitchen.partner_id),
        before=before,
        after={"grade": grade, "inspected_on": inspected_on.isoformat(), "note": note},
    )
    return kitchen


# --- capacity ------------------------------------------------------------------------------------


def locks_at(day: date) -> datetime:
    """When capacity for `day` closes: 21:00 IST the evening before."""
    return datetime.combine(day - timedelta(days=1), CUTOFF, IST)


def is_locked(day: date) -> bool:
    return now() >= locks_at(day)


def default_for(kitchen: Kitchen, day: date, meal: str) -> int:
    return int(kitchen.default_portions.get(meal, [0] * DAYS_IN_WEEK)[day.weekday()])


@dataclass(frozen=True)
class Capacity:
    on_date: date
    meal: str
    max_portions: int
    source: str  # default | set
    locked: bool


def window(start: date | None, end: date | None) -> tuple[date, date]:
    """`from` and `to` for a read: within 14 days from today, at most 14 days long; default the
    next 7 days."""
    first = start or today()
    if not today() <= first < today() + timedelta(days=WINDOW_DAYS):
        raise capacity_invalid("Ask for up to 14 days from today.")
    last = end or first + timedelta(days=DEFAULT_DAYS - 1)
    if last < first or (last - first).days >= WINDOW_DAYS:
        raise capacity_invalid("Ask for up to 14 days from today.")
    return first, last


async def overrides(
    session: AsyncSession, partner_ids: list[uuid.UUID], first: date, last: date
) -> dict[tuple[uuid.UUID, date, str], int]:
    if not partner_ids:
        return {}
    rows = await session.execute(
        select(KitchenCapacity).where(
            KitchenCapacity.partner_id.in_(partner_ids),
            KitchenCapacity.on_date.between(first, last),
        )
    )
    return {(c.partner_id, c.on_date, c.meal): c.max_portions for c in rows.scalars()}


async def capacities(
    session: AsyncSession, kitchen: Kitchen, first: date, last: date
) -> list[Capacity]:
    """Both meals of every date from `first` to `last`."""
    found = await overrides(session, [kitchen.partner_id], first, last)
    items = []
    for offset in range((last - first).days + 1):
        day = first + timedelta(days=offset)
        for meal in MEALS:
            stored = found.get((kitchen.partner_id, day, meal))
            items.append(
                Capacity(
                    day,
                    meal,
                    default_for(kitchen, day, meal) if stored is None else stored,
                    "default" if stored is None else "set",
                    is_locked(day),
                )
            )
    return items


async def set_capacity(
    session: AsyncSession, kitchen: Kitchen, day: date, meal: str, max_portions: int
) -> Capacity:
    """Override one date and meal (0 = no tiffins); 409 `partners.capacity_locked` after the
    evening-before cut-off."""
    if meal not in MEALS:
        raise capacity_invalid("The meal is lunch or dinner.")
    if not 0 <= max_portions <= MAX_PORTIONS:
        raise capacity_invalid("Capacity is 0 to 100 portions.")
    if not today() <= day < today() + timedelta(days=WINDOW_DAYS):
        raise capacity_invalid("Set capacity for up to 14 days from today.")
    if is_locked(day):
        raise ProblemError(
            409, "partners.capacity_locked", "Capacity for this date closed at 21:00 yesterday."
        )
    await session.execute(
        insert(KitchenCapacity)
        .values(
            {
                KitchenCapacity.partner_id: kitchen.partner_id,
                KitchenCapacity.on_date: day,
                KitchenCapacity.meal: meal,
                KitchenCapacity.max_portions: max_portions,
            }
        )
        .on_conflict_do_update(
            index_elements=["partner_id", "date", "meal"],
            set_={"max_portions": max_portions, "updated_at": now()},
        )
    )
    return Capacity(day, meal, max_portions, "set", False)


async def assigned_portions(
    session: AsyncSession, partner_id: uuid.UUID, first: date, last: date
) -> dict[tuple[date, str], int]:
    """Portions in her live (offered, accepted, preparing, ready) assignments per date and meal."""
    rows = await session.execute(
        select(Assignment.on_date, Assignment.meal, func.sum(Assignment.portions))
        .where(
            Assignment.partner_id == partner_id,
            Assignment.on_date.between(first, last),
            Assignment.status.in_(LIVE),
        )
        .group_by(Assignment.on_date, Assignment.meal)
    )
    return {(day, meal): int(portions) for day, meal, portions in rows.all()}
