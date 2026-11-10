"""M7's ops probes (M0 §6.1, spec §6): a failed route plan and one left for review, as the
`ops_check` watchdog sees them at its times (06:30, 12:30, 21:15, 23:45 IST)."""

import uuid
from datetime import date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import jobs
from gharbasket.modules.logistics.models import RoutePlan

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TODAY, TOMORROW = date(2026, 11, 10), date(2026, 11, 11)
HUB, OTHER = uuid.UUID(int=1), uuid.UUID(int=2)


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 10, hour, minute, tzinfo=jobs.IST)


def plan(hub: uuid.UUID, day: date, status: str, kind: str = "box", **more: Any) -> RoutePlan:
    more.setdefault("trigger", "job")
    return RoutePlan(hub_id=hub, delivery_date=day, kind=kind, status=status, **more)


async def test_a_failed_plan_for_the_day_being_prepared_is_reported_until_one_is_published(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            plan(HUB, TOMORROW, "failed", error="no orders with a location"),
            plan(HUB, TOMORROW, "failed", trigger="manual", error="no active vehicle"),  # a retry
            plan(HUB, TOMORROW, "published", kind="tiffin"),  # another kind clears nothing
            plan(HUB, TODAY, "published"),  # nor another date
            plan(OTHER, TOMORROW, "failed", error="no orders with a location"),
            plan(OTHER, TOMORROW, "published"),  # re-solved by hand and published
            plan(HUB, TODAY, "failed", kind="tiffin", error="no two-wheeler"),
        ]
    )
    await db_session.flush()

    after_the_solve = await jobs.plans_failed(db_session, at(23, 45))
    by_day = await jobs.plans_failed(db_session, at(12, 30))
    after_the_routes = await jobs.plans_failed(db_session, at(21, 15))

    # one line per hub, date and kind: the latest try
    assert after_the_solve == [f"box plan for {TOMORROW} at hub {HUB} failed: no active vehicle"]
    assert by_day == [f"tiffin plan for {TODAY} at hub {HUB} failed: no two-wheeler"]
    assert after_the_routes == []  # today's routes are closed, tomorrow's not solved yet


async def test_a_plan_left_for_review_is_reported_from_0630_on_its_day(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            plan(HUB, TODAY, "solved"),
            plan(OTHER, TODAY, "solved"),
            plan(OTHER, TODAY, "published"),
            plan(HUB, TOMORROW, "solved"),  # its manager has until tomorrow morning
        ]
    )
    await db_session.flush()

    assert await jobs.plans_unreviewed(db_session, at(6, 0)) == []
    assert await jobs.plans_unreviewed(db_session, at(6, 30)) == [
        f"box plan for {TODAY} at hub {HUB} is waiting for review; see /routes"
    ]
