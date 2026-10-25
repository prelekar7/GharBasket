"""The hub cut-off monitor and delivery dates (spec §3.4): reads only.

Cycles give what customers decided (awaiting, approved, auto-approved, skipped); `orders.api`
gives what the cut-off did (locked, payment needed, paid in the grace period, cancelled). A cycle
whose order is locked or `payment_failed` is counted on the order side only, so a household is
never counted twice. Dates are IST calendar dates and "now" is the API's own clock, so a moved
clock (e2e, `just sim`) shows the same day everywhere."""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import SocietyDeliveryDay, WeeklyCycle
from gharbasket.modules.subscriptions.schemas import (
    CutoffCountsOut,
    CutoffRunOut,
    HubCutoffOut,
    HubDeliveryDateOut,
    HubDeliveryDatesOut,
    ShortOrderOut,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import JobRun

CUTOFF_JOB = "cutoff_lock"


async def weekday_societies(session: AsyncSession, hub_id: uuid.UUID) -> dict[int, int]:
    """For each weekday (0 = Monday) the number of the hub's societies that deliver on it."""
    society_ids = await community_api.hub_society_ids(session, hub_id)
    if not society_ids:
        return {}
    rows = await session.execute(
        select(SocietyDeliveryDay.weekday, func.count())
        .where(SocietyDeliveryDay.society_id.in_(society_ids))
        .group_by(SocietyDeliveryDay.weekday)
    )
    return {weekday: count for weekday, count in rows.all()}


async def delivery_dates(
    session: AsyncSession, hub_id: uuid.UUID, start: date | None, days: int
) -> HubDeliveryDatesOut:
    """Every date from `start` (default today, IST) over `days` calendar days on which a society
    of the hub has a delivery day, soonest first."""
    start = start or service.IST_DATE()
    end = start + timedelta(days=days - 1)
    societies = await weekday_societies(session, hub_id)
    items = [
        HubDeliveryDateOut(
            date=day, societies=societies[day.weekday()], cutoff_at=orders_api.cutoff_for(day)
        )
        for day in (start + timedelta(days=i) for i in range(days))
        if day.weekday() in societies
    ]
    return HubDeliveryDatesOut.model_validate(
        {"hub_id": hub_id, "from": start, "to": end, "items": items}
    )


def not_delivery_date() -> ProblemError:
    return ProblemError(
        404, "subscriptions.not_delivery_date", "No society of this hub delivers on that date."
    )


async def last_run(session: AsyncSession, delivery_date: date) -> JobRun | None:
    """The first successful `cutoff_lock` run for the date, as the job itself maps a run to its
    delivery day (the IST day of `scheduled_for`, plus one). A re-run only counts what it found
    already locked, so the first run is the one that shows."""
    cutoff_on = delivery_date - timedelta(days=1)
    day_start = datetime.combine(cutoff_on, datetime.min.time(), service.IST)
    return (
        await session.execute(
            select(JobRun)
            .where(
                JobRun.name == CUTOFF_JOB,
                JobRun.status == "ok",
                JobRun.scheduled_for >= day_start,
                JobRun.scheduled_for < day_start + timedelta(days=1),
            )
            .order_by(JobRun.started_at)
            .limit(1)
        )
    ).scalar_one_or_none()


def _run_out(run: JobRun, delivery_date: date, orders: orders_api.CutoffCounts) -> CutoffRunOut:
    summary = run.summary or {}
    locked = int(summary.get("locked", 0))
    failed = int(summary.get("payment_failed", 0))
    already = int(summary.get("skipped_already_locked", 0))
    seconds = (run.finished_at - run.started_at).total_seconds() if run.finished_at else 0
    return CutoffRunOut(
        cutoff_on=delivery_date - timedelta(days=1),
        locked=locked,
        total=locked + failed + already,
        duration_s=max(0, round(seconds)),
        grace_paid=orders.grace_paid,
        cancelled=orders.cancelled,
    )


async def _short(
    session: AsyncSession, failed: tuple[orders_api.PaymentFailedOrder, ...]
) -> list[ShortOrderOut]:
    """The unpaid orders with the wallet read live, flat and society as display labels only."""
    if not failed:
        return []
    labels = await identity_api.delivery_labels(session, {o.household_id for o in failed})
    societies = await community_api.society_labels(session, list({o.society_id for o in failed}))
    out: list[ShortOrderOut] = []
    for order in failed:
        owner = await identity_api.owner_of_household(session, order.household_id)
        wallet = 0 if owner is None else await billing_api.balance(session, owner)
        society = societies.get(order.society_id)
        label = labels.get(order.household_id)
        tower = society.towers.get(label.tower_id) if society and label else None
        flat = "" if label is None else f"{tower}, {label.flat}" if tower else label.flat
        out.append(
            ShortOrderOut(
                order_id=order.order_id,
                society=society.name if society else "",
                flat=flat,
                box_paise=order.total_paise,
                wallet_paise=wallet,
                short_paise=max(0, order.total_paise - wallet),
            )
        )
    return sorted(out, key=lambda s: (-s.short_paise, str(s.order_id)))


async def monitor(session: AsyncSession, hub_id: uuid.UUID, requested: date | None) -> HubCutoffOut:
    """The cut-off monitor of a delivery day (default: the hub's next delivery date)."""
    weekdays = await weekday_societies(session, hub_id)
    if requested is None:
        today = service.IST_DATE()
        requested = next(
            (
                day
                for day in (today + timedelta(days=i) for i in range(1, 8))
                if day.weekday() in weekdays
            ),
            None,
        )
    if requested is None or requested.weekday() not in weekdays:
        raise not_delivery_date()

    orders = await orders_api.hub_cutoff_counts(session, hub_id, requested)
    cycles = (
        await session.execute(
            select(WeeklyCycle.id, WeeklyCycle.status, WeeklyCycle.auto_approved).where(
                WeeklyCycle.hub_id == hub_id, WeeklyCycle.delivery_date == requested
            )
        )
    ).all()
    awaiting = approved = auto = skipped = 0
    for cycle_id, status, auto_approved in cycles:
        auto += auto_approved and status in ("approved", "locked")
        if cycle_id in orders.settled_cycle_ids:
            continue  # counted on the order side
        if status in ("draft", "awaiting_approval"):
            awaiting += 1
        elif status == "approved":
            approved += 1
        elif status == "skipped":
            skipped += 1
    run = await last_run(session, requested)
    return HubCutoffOut(
        hub_id=hub_id,
        delivery_date=requested,
        cutoff_at=orders_api.cutoff_for(requested),
        grace_until=orders_api.grace_until_for(requested),
        as_of=service.now(),
        counts=CutoffCountsOut(
            awaiting=awaiting,
            approved=approved,
            auto_approved=auto,
            skipped=skipped,
            locked=orders.locked,
            payment_needed=orders.payment_needed,
        ),
        last_run=None if run is None else _run_out(run, requested, orders),
        short=await _short(session, orders.payment_failed),
    )
