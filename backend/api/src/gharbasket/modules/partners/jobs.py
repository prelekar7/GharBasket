"""Jobs of M8 Partner Network (spec §6, ADR-0021). `scheduled_for` decides the delivery day T,
never the clock, and stands in for "now" in the rounds' reply deadlines.

- `assign_kitchens` (T-1 21:30 IST): round 1 for every active hub, both meals of T.
"""

from datetime import date, timedelta
from typing import Any

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.partners import assignments
from gharbasket.platform.jobs import JobContext, job


async def _rounds(ctx: JobContext, day: date) -> dict[str, int]:
    offers = unassigned = runs = 0
    for hub_id in await hub_ops_api.active_hub_ids(ctx.session):
        result = await assignments.run_round(
            ctx.session, ctx.http, ctx.settings, hub_id, day, at=ctx.scheduled_for
        )
        runs, offers = runs + len(result.run_ids), offers + result.offers
        unassigned += result.unassigned
    return {"runs": runs, "offers": offers, "unassigned": unassigned}


@job("assign_kitchens", "Round 1 of tomorrow's tiffin assignment at every active hub (21:30 IST)")
async def assign_kitchens(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(assignments.IST).date() + timedelta(days=1)
    return {"delivery_date": day.isoformat(), **await _rounds(ctx, day)}
