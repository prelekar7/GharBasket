"""community.api, the only way M1 reaches societies (M9 §3.1 facade)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("societies")]

SAI_DARSHAN = uuid.UUID("0199a000-0000-7000-8000-00000000b006")  # seed: no boundary yet


async def test_get_society_gives_what_an_address_needs(db_session: AsyncSession) -> None:
    society = await api.get_society(db_session, SEED_SOCIETY_ID)

    assert society == api.SocietyRef(
        id=SEED_SOCIETY_ID, hub_id=SEED_HUB_ID, status="live", tower_ids=frozenset({"a", "b", "c"})
    )
    assert await api.get_society(db_session, uuid.uuid4()) is None


@pytest.mark.parametrize(
    ("society_id", "lat", "lng", "inside"),
    [
        (SEED_SOCIETY_ID, 19.0395, 73.0675, True),  # centre
        (SEED_SOCIETY_ID, 19.0430, 73.0720, False),  # Shree Ganesh, ~600 m away
        (SAI_DARSHAN, 18.99, 73.11, True),  # no boundary drawn: anywhere is accepted
        (uuid.UUID(int=1), 19.0395, 73.0675, False),  # unknown society
    ],
)
async def test_contains_checks_the_pin_against_the_boundary(
    db_session: AsyncSession, society_id: uuid.UUID, lat: float, lng: float, inside: bool
) -> None:
    assert await api.contains(db_session, society_id, lat, lng) is inside
