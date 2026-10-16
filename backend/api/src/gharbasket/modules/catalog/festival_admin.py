"""The console's festival calendar (spec §3.3): list, add, edit and delete festival days.

Days added in the console have `source: manual`; edits set `updated_by`. The seed never
overwrites a manual, verified or console-edited day (`recipes.save_festival`). A day that has
started is never deleted, because plans may already rest on it. Every write is audit-logged.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.admin import checked_names
from gharbasket.modules.catalog.models import REGIONS, Festival
from gharbasket.modules.catalog.pricing import today
from gharbasket.modules.catalog.schemas import FestivalIn, FestivalPatch
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError

RANGE_DAYS = 366
HORIZON = timedelta(days=730)  # a day is within two years of today


def _key(festival: Festival) -> str:
    return f"{festival.day.isoformat()}/{festival.code}/{festival.region}"


def _snapshot(festival: Festival) -> dict[str, Any]:
    return {
        "date": festival.day.isoformat(),
        "code": festival.code,
        "region": festival.region,
        "names": festival.names,
        "rules": festival.rules,
        "verified": festival.verified,
        "source": festival.source,
    }


async def list_days(
    session: AsyncSession, start: date | None, end: date | None, region: str | None
) -> list[Festival]:
    first = start or today()
    last = end or first + timedelta(days=30)
    if last < first or (last - first).days > RANGE_DAYS:
        raise ProblemError(422, "request.invalid", f"Use a range of 0 to {RANGE_DAYS} days.")
    stmt = select(Festival).where(Festival.day >= first, Festival.day <= last)
    if region:
        stmt = stmt.where(Festival.region == region)
    order = (Festival.day, Festival.code, Festival.region)
    return list((await session.execute(stmt.order_by(*order))).scalars())


async def get(session: AsyncSession, day: date, code: str, region: str) -> Festival:
    festival = await session.get(Festival, (day, code, region), with_for_update=True)
    if festival is None:
        raise ProblemError(404, "catalog.festival_not_found", "No such festival day.")
    return festival


async def create(session: AsyncSession, body: FestivalIn, actor: uuid.UUID) -> Festival:
    if body.region != "all" and body.region not in REGIONS:
        raise ProblemError(422, "request.invalid", "Use 'all' or a state code such as maharashtra.")
    if abs(body.date - today()) > HORIZON:
        raise ProblemError(422, "request.invalid", "A festival day is within two years of today.")
    exists = ProblemError(409, "catalog.festival_exists", "This day is already in the calendar.")
    if await session.get(Festival, (body.date, body.code, body.region)):
        raise exists
    festival = Festival(
        day=body.date,
        code=body.code,
        region=body.region,
        names=checked_names(body.names),
        rules=body.rules.model_dump(),
        verified=body.verified,
        source="manual",
        updated_by=actor,
    )
    try:
        async with session.begin_nested():  # two admins adding the same day at once
            session.add(festival)
            await session.flush()
    except IntegrityError:
        raise exists from None
    await _record(session, festival, actor, "created", None, _snapshot(festival))
    return festival


async def update(
    session: AsyncSession, day: date, code: str, region: str, body: FestivalPatch, actor: uuid.UUID
) -> Festival:
    """Last write wins (a day is a few fields); only the fields sent change."""
    if nulls := sorted(n for n in body.model_fields_set if getattr(body, n) is None):
        raise ProblemError(422, "request.invalid", f"These cannot be null: {', '.join(nulls)}.")
    festival = await get(session, day, code, region)
    before = _snapshot(festival)
    if body.names is not None:
        festival.names = checked_names(body.names)
    if body.rules is not None:
        festival.rules = body.rules.model_dump()
    if body.verified is not None:
        festival.verified = body.verified
    after = _snapshot(festival)
    if changed := [name for name in after if after[name] != before[name]]:
        festival.updated_by = actor
        await session.flush()
        await _record(
            session,
            festival,
            actor,
            "updated",
            {name: before[name] for name in changed},
            {name: after[name] for name in changed},
        )
    return festival


async def delete(
    session: AsyncSession, day: date, code: str, region: str, actor: uuid.UUID
) -> None:
    festival = await get(session, day, code, region)
    if festival.day <= today():
        raise ProblemError(
            409, "catalog.festival_past", "This day has started; plans may already rest on it."
        )
    before = _snapshot(festival)
    await session.delete(festival)
    await session.flush()
    await audit.record(
        session,
        actor_user_id=actor,
        action="catalog.festival_deleted",
        entity_type="festival",
        entity_id=_key(festival),
        before=before,
    )


async def _record(
    session: AsyncSession,
    festival: Festival,
    actor: uuid.UUID,
    verb: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    await audit.record(
        session,
        actor_user_id=actor,
        action=f"catalog.festival_{verb}",
        entity_type="festival",
        entity_id=_key(festival),
        before=before,
        after=after,
    )
    await session.refresh(festival)  # server-side updated_at
