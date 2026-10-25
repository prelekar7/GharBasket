"""Fact rows of M8 for M12's analytics refresh (M12 §3.1): partners (kind, hub, status) and
assignment runs (the fairness numbers). No name, phone, photo or KYC field."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.partners.models import AssignmentRun, Partner


@dataclass(frozen=True)
class PartnerFact:
    partner_id: uuid.UUID
    hub_id: uuid.UUID
    kind: str  # kitchen | captain | rider | packer
    status: str  # active | paused | suspended | left
    data_source: str


@dataclass(frozen=True)
class AssignmentRunFact:
    run_id: uuid.UUID
    hub_id: uuid.UUID
    on: date
    meal: str  # lunch | dinner
    round: int
    source: str  # ml | fallback
    portions: int
    gini_bp: int | None  # the Gini of the request's kitchens' earnings, in basis points
    km_per_portion_m: int | None
    data_source: str  # the hub's


async def partner_facts(
    session: AsyncSession, *, ids: Iterable[uuid.UUID] | None = None
) -> list[PartnerFact]:
    """Every partner record (or only `ids`), in id order."""
    stmt = select(Partner.id, Partner.hub_id, Partner.kind, Partner.status, Partner.data_source)
    if ids is not None:
        stmt = stmt.where(Partner.id.in_(set(ids)))
    rows = await session.execute(stmt.order_by(Partner.id))
    return [PartnerFact(*row) for row in rows]


def _scaled(value: object, factor: int) -> int | None:
    return round(float(value) * factor) if isinstance(value, int | float) else None


async def assignment_run_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[AssignmentRunFact]:
    """Assignment runs for dates between the two IST dates (inclusive), in id order."""
    stmt = select(AssignmentRun).where(
        AssignmentRun.on_date >= date_from, AssignmentRun.on_date <= date_to
    )
    if hub_ids is not None:
        stmt = stmt.where(AssignmentRun.hub_id.in_(hub_ids))
    runs = list((await session.execute(stmt.order_by(AssignmentRun.id))).scalars())
    sources = await hub_ops_api.hub_data_sources(session, {run.hub_id for run in runs})
    return [
        AssignmentRunFact(
            run_id=run.id,
            hub_id=run.hub_id,
            on=run.on_date,
            meal=run.meal,
            round=run.round,
            source=run.source,
            portions=run.portions,
            gini_bp=_scaled((run.summary or {}).get("gini"), 10_000),
            km_per_portion_m=_scaled((run.summary or {}).get("km_per_portion"), 1_000),
            data_source=sources.get(run.hub_id, "real"),
        )
        for run in runs
    ]
