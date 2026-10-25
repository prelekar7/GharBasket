"""community.api, the only way M1 reaches societies (M9 §3.1 facade)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("societies")]

SAI_DARSHAN = uuid.UUID("0199a000-0000-7000-8000-00000000b006")  # seed: no boundary yet
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")  # seed: unlocking


async def test_get_society_gives_what_an_address_needs(db_session: AsyncSession) -> None:
    society = await api.get_society(db_session, SEED_SOCIETY_ID)

    assert society is not None and society.live_at is not None  # live: set when it went live
    assert society == api.SocietyRef(
        id=SEED_SOCIETY_ID,
        hub_id=SEED_HUB_ID,
        status="live",
        tower_ids=frozenset({"a", "b", "c"}),
        name="Sunrise Towers",
        live_at=society.live_at,
    )
    unlocking = await api.get_society(db_session, GREEN_VALLEY)
    assert unlocking is not None and (unlocking.status, unlocking.live_at) == ("unlocking", None)
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


async def test_hub_society_ids_are_the_societies_a_hub_serves_whatever_their_status(
    db_session: AsyncSession,
) -> None:
    served = await api.hub_society_ids(db_session, SEED_HUB_ID)

    # The five seed societies with a hub (live, unlocking and prospect); Sai Darshan has none.
    assert len(served) == 5 and SEED_SOCIETY_ID in served and GREEN_VALLEY in served
    assert SAI_DARSHAN not in served
    assert await api.hub_society_ids(db_session, uuid.uuid4()) == []
