"""Facade of M6 Hub Operations: the only module other modules may import.

M6 owns hub ids (ADR-0026 §5): other modules keep them as plain UUIDs and look hubs up here.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import service
from gharbasket.modules.hub_ops.models import Hub


@dataclass(frozen=True)
class HubRef:
    id: uuid.UUID
    slug: str
    name: str
    status: str  # active | paused
    lat: float
    lng: float
    timings: dict[str, str]  # receiving, packing, dispatch, surplus as IST "HH:MM"


async def get_hub(session: AsyncSession, hub_id: uuid.UUID) -> HubRef | None:
    hub = await session.get(Hub, hub_id)
    if hub is None:
        return None
    return HubRef(
        id=hub.id,
        slug=hub.slug,
        name=hub.name,
        status=hub.status,
        lat=hub.lat,
        lng=hub.lng,
        timings=dict(hub.timings),
    )


async def active_hub_ids(session: AsyncSession) -> list[uuid.UUID]:
    """Hubs that are not paused, for jobs that run per hub."""
    return await service.active_hub_ids(session)
