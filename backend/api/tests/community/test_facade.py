"""community.api, the only way M1 reaches societies (M9 §3.1 facade)."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api
from gharbasket.modules.identity import api as identity_api
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, seed, seed_households

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


async def test_hub_societies_give_name_status_and_unlock_progress_and_nothing_personal(
    db_session: AsyncSession,
) -> None:
    await seed(db_session)
    await seed_households(db_session)  # the seeded customers, so `joined` is not all 0
    listed = await api.hub_societies(db_session, hub_id=SEED_HUB_ID)

    assert [s.name for s in listed] == sorted(s.name for s in listed)
    assert {s.id for s in listed} == set(await api.hub_society_ids(db_session, SEED_HUB_ID))
    by_id = {s.id: s for s in listed}
    sunrise, green = by_id[SEED_SOCIETY_ID], by_id[GREEN_VALLEY]
    assert (sunrise.name, sunrise.status) == ("Sunrise Towers", "live")
    assert green.status == "unlocking" and green.threshold > 0
    assert all(s.data_source == "synthetic" for s in listed)  # the seed's
    # One count for every society at once, the same as M1's count for each
    for society in listed:
        assert society.joined == await identity_api.households_in_society(db_session, society.id)
    assert any(s.joined > 0 for s in listed)
    assert await identity_api.households_in_societies(db_session, []) == {}
    assert await identity_api.households_in_societies(db_session, [SAI_DARSHAN, SAI_DARSHAN]) == {
        SAI_DARSHAN: await identity_api.households_in_society(db_session, SAI_DARSHAN)
    }
    assert {f for f in api.HubSociety.__dataclass_fields__} == {
        "id",
        "name",
        "status",
        "joined",
        "threshold",
        "data_source",
    }
    assert await api.hub_societies(db_session, hub_id=uuid.uuid4()) == []


async def test_hub_society_cards_are_name_area_and_status_live_first_and_nothing_personal(
    db_session: AsyncSession,
) -> None:
    cards = await api.hub_society_cards(db_session, SEED_HUB_ID)

    # Live, unlocking and prospect, each by name; Sai Darshan has no hub.
    assert [(c.name, c.status) for c in cards] == [
        ("Krishna Kunj", "live"),
        ("Sunrise Towers", "live"),
        ("Green Valley Heights", "unlocking"),
        ("Shree Ganesh CHS", "unlocking"),
        ("Panchavati Residency", "prospect"),
    ]
    sunrise = next(c for c in cards if c.id == SEED_SOCIETY_ID)
    assert sunrise.area == "Sector 20, Kharghar"
    assert set(api.SocietyCard.__dataclass_fields__) == {"id", "name", "area", "status"}
    assert {c.id for c in cards} == set(await api.hub_society_ids(db_session, SEED_HUB_ID))
    assert await api.hub_society_cards(db_session, uuid.uuid4()) == []
