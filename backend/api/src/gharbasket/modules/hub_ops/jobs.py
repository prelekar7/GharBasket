"""Jobs of M6 Hub Operations (spec §6, ADR-0021): `build_packing_tasks` at T 05:00 IST.

`scheduled_for` decides the delivery date (its IST calendar day), never the clock.
"""

from typing import Any

from gharbasket.modules.hub_ops import packing, service
from gharbasket.platform.jobs import JobContext, job


@job("build_packing_tasks", "Build the day's packing tasks at every active hub (T 05:00 IST)")
async def build_packing_tasks(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(packing.IST).date()
    hubs = await service.active_hub_ids(ctx.session)
    created = existing = 0
    for hub_id in hubs:
        made, had = await packing.build(ctx.session, hub_id, day)
        created, existing = created + made, existing + had
    return {
        "delivery_date": day.isoformat(),
        "hubs": len(hubs),
        "created": created,
        "existing": existing,
    }
