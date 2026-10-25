"""The hub's today board (spec §3.3): live counts through the source modules' facades, never the
views, so the board is as fresh as the modules themselves. Dates are IST and "now" is the API's
own clock.

Attention rows are sent only for kinds that have a source and a count above 0. `donations_to_verify`
(M9 has no facade for the queue yet), `surplus_open` (M6 has none for open offers) and
`society_live` (nothing says what needs attention) are never sent.
"""

import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.reads import IST
from gharbasket.modules.analytics.schemas import (
    AttentionKind,
    BoardAttentionOut,
    BoardBoxesOut,
    BoardDeliveriesOut,
    BoardPackingOut,
    BoardRoutesOut,
    BoardShortageOut,
    BoardStepOut,
    BoardWasteOut,
    SocietyBoxesOut,
    StepKey,
    TodayOut,
)
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised

DAYS_BACK, DAYS_AHEAD = 7, 1  # the dates the board answers: today - 7 ... today + 1
WASTE_LOOKBACK_DAYS = 14  # how far back "the previous day with data" is looked for
CUTOFF_DUE = "21:00"  # the day before; the cut-off's own time (orders.api.cutoff_for)
LINKS: dict[AttentionKind, str] = {
    "issue_open": "/hub/issues?status=open",
    "payment_failed": "/hub/cutoff",
    "shortage": "/hub/packing",
    "plan_needs_review": "/hub/routes",
    "delivery_held": "/hub/deliveries",
    "donations_to_verify": "/community/donations",
    "surplus_open": "/hub/waste",
}


def date_in_range(day: date, today: date) -> bool:
    return today - timedelta(days=DAYS_BACK) <= day <= today + timedelta(days=DAYS_AHEAD)


def _clock(on: date, hhmm: str) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime.combine(on, time(int(hours), int(minutes)), IST)


def _state(*, done: bool, issue: bool, started: bool) -> str:
    return "done" if done else "issue" if issue else "current" if started else "todo"


async def board(
    session: AsyncSession, hub_id: uuid.UUID, on: date, lang: Language, now: datetime
) -> TodayOut:
    hub = await hub_ops_api.get_hub(session, hub_id)
    if hub is None:
        raise ProblemError(404, "hub_ops.hub_not_found", "No such hub.")
    counts = await orders_api.today_counts(session, hub_id=hub_id, on=on)
    issues = await orders_api.open_issue_summary(session, hub_id=hub_id)
    packing = await hub_ops_api.packing_progress(session, hub_id=hub_id, on=on)
    inbound = await hub_ops_api.inbound_progress(session, hub_id=hub_id, on=on)
    routes = await logistics_api.today_routes(session, hub_id=hub_id, on=on, now=now)
    days = await hub_ops_api.waste_summary(
        session, hub_id, on - timedelta(days=WASTE_LOOKBACK_DAYS), on
    )

    labels = await community_api.society_labels(session, list(counts.boxes_by_society))
    by_society = sorted(
        (
            SocietyBoxesOut(
                society_id=society_id,
                name=labels[society_id].name if society_id in labels else "",
                boxes=boxes,
            )
            for society_id, boxes in counts.boxes_by_society.items()
        ),
        key=lambda s: (s.name.casefold(), str(s.society_id)),
    )
    products = await catalog_api.stock_products(session, [s.product_id for s in packing.shortages])
    shortages = [
        BoardShortageOut(
            product_id=s.product_id,
            name=localised(products[s.product_id].names, lang) if s.product_id in products else "",
            bags=s.bags,
        )
        for s in packing.shortages
    ]
    today_waste = next((d for d in days if d.date == on), None)
    previous = next(
        (d for d in reversed(days) if d.date < on and (d.sold_kg > 0 or d.wasted_kg > 0)), None
    )

    return TodayOut(
        hub_id=hub_id,
        date=on,
        as_of=now,
        boxes=BoardBoxesOut(
            total=counts.boxes_total,
            locked=counts.boxes_locked,
            cutoff_at=counts.cutoff_at,
            by_society=by_society,
        ),
        packing=BoardPackingOut(
            bags_total=packing.bags_total,
            bags_packed=packing.bags_packed,
            finished_at=packing.finished_at,
        ),
        shortages=shortages,
        routes=BoardRoutesOut(
            planned=routes.planned,
            started=routes.started,
            completed=routes.completed,
            stops_total=routes.stops_total,
            stops_done=routes.stops_done,
            late_stops=routes.late_stops,
        ),
        deliveries=BoardDeliveriesOut(
            at_society=routes.at_society,
            delivered=routes.delivered,
            held=routes.held,
            undelivered=routes.undelivered,
        ),
        waste=BoardWasteOut(
            kg=0.0 if today_waste is None else today_waste.wasted_kg,
            previous_kg=None if previous is None else previous.wasted_kg,
            previous_date=None if previous is None else previous.date,
        ),
        steps=steps(hub.timings, on, now, counts, packing, inbound, routes),
        attention=attention(on, counts, issues, packing, routes),
    )


def steps(
    timings: dict[str, str],
    on: date,
    now: datetime,
    counts: orders_api.TodayCounts,
    packing: hub_ops_api.PackingProgress,
    inbound: hub_ops_api.InboundProgress,
    routes: logistics_api.TodayRoutes,
) -> list[BoardStepOut]:
    """The hub's day in order. A step is `done` when its work is finished, `issue` when it is not
    and its time has passed (or something went wrong), `current` when it has begun, else `todo`.
    Due times are the hub's timings; the cut-off's is 21:00 the day before. Procurement and
    delivery have no timing of their own (due_at null), and surplus has no status to read yet, so
    it is `done` once its time has passed."""
    receiving_at, packing_at = _clock(on, timings["receiving"]), _clock(on, timings["packing"])
    dispatch_at, surplus_at = _clock(on, timings["dispatch"]), _clock(on, timings["surplus"])
    failed = counts.by_status.get("payment_failed", 0)
    cutoff_passed = now >= counts.cutoff_at
    pos_sent = inbound.pos_sent

    def step(
        key: StepKey, due: str | None, *, done: bool, issue: bool, started: bool, note: str | None
    ) -> BoardStepOut:
        return BoardStepOut(
            key=key,
            due_at=due,
            state=_state(done=done, issue=issue, started=started),  # pyright: ignore[reportArgumentType]
            note=note,
        )

    return [
        step(
            "cutoff",
            CUTOFF_DUE,
            done=cutoff_passed and failed == 0,
            issue=cutoff_passed and failed > 0,
            started=now.astimezone(IST).date() >= on - timedelta(days=1),
            note=f"{failed} payment failed" if failed else None,
        ),
        step(
            "procurement",
            None,
            done=inbound.plan_status in ("ordered", "closed")
            or (pos_sent > 0 and not inbound.pos_draft),
            issue=now >= receiving_at and inbound.pos_draft > 0,
            started=inbound.plan_status is not None,
            note=f"{inbound.pos_draft} orders not sent" if inbound.pos_draft else None,
        ),
        step(
            "receiving",
            timings["receiving"],
            done=pos_sent > 0 and inbound.pos_received == pos_sent,
            issue=now >= packing_at and pos_sent > inbound.pos_received,
            started=now >= receiving_at or inbound.pos_received > 0,
            note=f"{inbound.pos_received} of {pos_sent} orders received" if pos_sent else None,
        ),
        step(
            "packing",
            timings["packing"],
            done=packing.bags_total > 0 and packing.bags_packed == packing.bags_total,
            issue=now >= dispatch_at and packing.bags_packed < packing.bags_total,
            started=now >= packing_at or packing.bags_packed > 0 or packing.bags_picking > 0,
            note=f"{packing.bags_packed} of {packing.bags_total} bags"
            if packing.bags_total
            else None,
        ),
        step(
            "dispatch",
            timings["dispatch"],
            done=routes.planned > 0 and routes.started == routes.planned,
            issue=now >= dispatch_at
            and counts.boxes_total > 0
            and routes.started < max(routes.planned, 1),
            started=now >= dispatch_at or routes.started > 0,
            note=f"{routes.started} of {routes.planned} routes started" if routes.planned else None,
        ),
        step(
            "delivery",
            None,
            done=routes.planned > 0 and routes.completed == routes.planned,
            issue=routes.late_stops > 0 or routes.undelivered > 0,
            started=routes.started > 0,
            note=f"{routes.stops_done} of {routes.stops_total} stops"
            if routes.stops_total
            else None,
        ),
        step(
            "surplus",
            timings["surplus"],
            done=now >= surplus_at,
            issue=False,
            started=False,
            note=None,
        ),
    ]


def attention(
    on: date,
    counts: orders_api.TodayCounts,
    issues: orders_api.OpenIssueSummary,
    packing: hub_ops_api.PackingProgress,
    routes: logistics_api.TodayRoutes,
) -> list[BoardAttentionOut]:
    """One row per kind that has a source and a count above 0."""
    rows: list[tuple[AttentionKind, int, datetime | None, datetime | None]] = [
        (
            "issue_open",
            issues.count,
            issues.oldest_created_at,
            issues.earliest_callback_due_at,
        ),
        (
            "payment_failed",
            counts.by_status.get("payment_failed", 0),
            counts.cutoff_at,  # they failed at the cut-off
            orders_api.grace_until_for(on),
        ),
        ("shortage", len(packing.shortages), None, None),
        ("plan_needs_review", routes.plans_to_review, routes.oldest_plan_at, None),
        ("delivery_held", routes.held, None, None),
    ]
    return [
        BoardAttentionOut(kind=kind, count=count, oldest_at=oldest, due_at=due, link=LINKS[kind])
        for kind, count, oldest, due in rows
        if count > 0
    ]
