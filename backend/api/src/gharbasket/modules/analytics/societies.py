"""The societies table (spec §3.3): the console's Customers page. M9 gives each society's name,
status and unlock progress (`hub_societies`); the aggregates are read in the analytics fact
tables over the last 4 weeks, today included. No household id leaves this module: M3 is handed
the ids of a society's households and answers which are paused, and only the count is sent.
"""

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import freshness
from gharbasket.modules.analytics.kpis import BP, ratio
from gharbasket.modules.analytics.models import FactCycle, FactHousehold, FactOrder
from gharbasket.modules.analytics.reads import read_limit
from gharbasket.modules.analytics.schemas import KpiSource, SocietiesOut, SocietyRowOut
from gharbasket.modules.community import api as community_api
from gharbasket.modules.subscriptions import api as subs_api

WINDOW_DAYS = 28  # "active" and the rates look at the last 4 weeks, today included


async def societies(
    session: AsyncSession, hub_id: uuid.UUID | None, source: KpiSource, today: date
) -> SocietiesOut:
    """The societies of the hub (every hub, for an admin who gives none) with the given data
    source, by name."""
    start = today - timedelta(days=WINDOW_DAYS - 1)
    hub_ids = [hub_id] if hub_id is not None else await community_api.hubs_with_societies(session)
    listed = [
        society
        for one in hub_ids
        for society in await community_api.hub_societies(session, hub_id=one)
        if society.data_source == source
    ]
    ids = [society.id for society in listed]
    async with read_limit(session):
        homes = defaultdict[uuid.UUID, list[uuid.UUID]](list)
        if ids:
            for household_id, society_id in (
                await session.execute(
                    select(FactHousehold.household_id, FactHousehold.society_id).where(
                        FactHousehold.society_id.in_(ids), FactHousehold.data_source == source
                    )
                )
            ).all():
                homes[society_id].append(household_id)
        paused = await subs_api.paused_households(
            session, household_ids=[h for members in homes.values() for h in members], on=today
        )
        active = {
            society_id: int(n)
            for society_id, n in (
                await session.execute(
                    select(FactOrder.society_id, func.count(FactOrder.household_id.distinct()))
                    .where(
                        FactOrder.society_id.in_(ids),
                        FactOrder.data_source == source,
                        FactOrder.status == "delivered",
                        FactOrder.delivery_date.between(start, today),
                    )
                    .group_by(FactOrder.society_id)
                )
            ).all()
        }
        cycles = {
            society_id: (int(on_time), int(generated))
            for society_id, on_time, generated in (
                await session.execute(
                    select(
                        FactCycle.society_id,
                        func.count().filter(FactCycle.approved_before_cutoff),
                        func.count(),
                    )
                    .where(
                        FactCycle.society_id.in_(ids),
                        FactCycle.data_source == source,
                        FactCycle.delivery_date.between(start, today),
                    )
                    .group_by(FactCycle.society_id)
                )
            ).all()
        }
        boxes = {
            society_id: (int(net), int(n))
            for society_id, net, n in (
                await session.execute(
                    select(
                        FactOrder.society_id,
                        func.sum(
                            FactOrder.total_paise
                            - FactOrder.refunded_paise
                            - FactOrder.credited_paise
                        ),
                        func.count(),
                    )
                    .where(
                        FactOrder.society_id.in_(ids),
                        FactOrder.data_source == source,
                        FactOrder.status == "delivered",
                        FactOrder.kind == "box",
                        FactOrder.delivery_date.between(start, today),
                    )
                    .group_by(FactOrder.society_id)
                )
            ).all()
        }
        fresh = await freshness.freshness(session, "mv_daily_hub_kpis")

    return SocietiesOut(
        hub_id=hub_id,
        as_of=fresh.as_of,
        items=[
            SocietyRowOut(
                society_id=society.id,
                name=society.name,
                status=society.status,  # pyright: ignore[reportArgumentType]
                joined=society.joined,
                threshold=society.threshold,
                active_households=active.get(society.id, 0),
                paused_households=sum(1 for h in homes.get(society.id, []) if h in paused),
                approved_on_time_bp=ratio(*cycles.get(society.id, (0, 0)), BP),
                average_box_paise=ratio(*boxes.get(society.id, (0, 0))[:2]),
            )
            for society in listed
        ],
    )
