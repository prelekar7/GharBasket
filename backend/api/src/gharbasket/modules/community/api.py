"""Facade of M9 Community & Kindness: the only module other modules may import.

M1 uses it to check a household's address (spec §3.1): the society exists, the tower belongs to
it, and the flat's pin lies inside its boundary.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society


@dataclass(frozen=True)
class SocietyRef:
    id: uuid.UUID
    hub_id: uuid.UUID | None
    status: str
    tower_ids: frozenset[str]


async def get_society(session: AsyncSession, society_id: uuid.UUID) -> SocietyRef | None:
    society = await session.get(Society, society_id)
    if society is None:
        return None
    return SocietyRef(
        id=society.id,
        hub_id=society.hub_id,
        status=society.status,
        tower_ids=frozenset(str(tower["id"]) for tower in society.towers),
    )


async def contains(session: AsyncSession, society_id: uuid.UUID, lat: float, lng: float) -> bool:
    """True if the point is inside (or on) the society's boundary, or no boundary is drawn yet.

    False for an unknown society.
    """
    here = func.ST_GeogFromText(f"SRID=4326;POINT({lng} {lat})")
    inside = await session.execute(
        select(func.coalesce(func.ST_Covers(Society.boundary, here), True)).where(
            Society.id == society_id
        )
    )
    return bool(inside.scalar_one_or_none())
