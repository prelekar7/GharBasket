"""Member and address edits after onboarding (M1 §3.2): each one bumps the household version."""

import uuid
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Address, Household
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, seed_societies

from .conftest import ClientFactory
from .fake_firebase import FakeGoogle
from .onboarding import ONBOARDING, customer, onboard, onboarding

pytestmark = [pytest.mark.anyio, pytest.mark.services]

GREEN_VALLEY = "0199a000-0000-7000-8000-00000000b002"  # seed: towers a and b, same hub
WITHOUT_HEALTH = ONBOARDING["consents"][:2]


@pytest.fixture(autouse=True)
async def societies(db_session: AsyncSession) -> None:
    await seed_societies(db_session)


async def _household(
    api: httpx2.AsyncClient, google: FakeGoogle, body: dict[str, Any] | None = None
) -> tuple[dict[str, str], dict[str, Any]]:
    headers = await customer(api, google)
    created = await onboard(api, headers, body)
    assert created.status_code == 201, created.text
    return headers, created.json()


async def _me(api: httpx2.AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    return (await api.get("/v1/households/me", headers=headers)).json()


async def _changes(db: AsyncSession, household_id: str) -> list[list[str]]:
    events = await db.execute(
        select(OutboxEvent.payload["changed"])
        .where(
            OutboxEvent.event_type == "identity.household_updated",
            OutboxEvent.payload["household_id"].astext == household_id,
        )
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
    )
    return list(events.scalars())


# --- members ----------------------------------------------------------------------------------


async def test_add_change_and_remove_a_member(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    members = f"/v1/households/{household['id']}/members"

    added = await api.post(members, json={"age_band": "5_17", "sex": "m"}, headers=headers)
    changed = await api.patch(
        f"{members}/{added.json()['id']}",
        json={"sex": None, "fasting_rules": {"fasts": ["navratri"]}},
        headers=headers,
    )
    removed = await api.delete(f"{members}/{added.json()['id']}", headers=headers)

    assert added.status_code == 201, added.text
    assert added.headers["etag"] == '"2"'
    assert added.json()["age_band"] == "5_17" and added.json()["health_tags"] == []
    assert changed.status_code == 200, changed.text
    assert changed.headers["etag"] == '"3"'
    assert changed.json()["sex"] is None
    assert changed.json()["fasting_rules"] == {
        "jain": False,
        "fasts": ["navratri"],
        "no_onion_garlic_on_fast_days": False,
    }
    assert removed.status_code == 204 and removed.headers["etag"] == '"4"'
    me = await _me(api, headers)
    assert (me["version"], me["size"], len(me["members"])) == (4, 2, 2)
    assert await _changes(db_session, household["id"]) == [
        ["members", "size"],
        ["members"],
        ["members", "size"],
    ]


async def test_a_change_that_changes_nothing_keeps_the_version(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    member = household["members"][1]

    same = await api.patch(
        f"/v1/households/{household['id']}/members/{member['id']}",
        json={"age_band": member["age_band"]},
        headers=headers,
    )

    assert same.status_code == 200 and same.headers["etag"] == '"1"'


async def test_the_last_member_cannot_be_removed(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers, household = await _household(
        api, google, onboarding(members=[{"age_band": "18_59"}], consents=WITHOUT_HEALTH)
    )

    response = await api.delete(
        f"/v1/households/{household['id']}/members/{household['members'][0]['id']}",
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "identity.household_needs_member"


async def test_a_household_has_at_most_twelve_members(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers, household = await _household(
        api, google, onboarding(members=[{"age_band": "18_59"}] * 12, consents=WITHOUT_HEALTH)
    )

    response = await api.post(
        f"/v1/households/{household['id']}/members", json={"age_band": "60_plus"}, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "identity.household_full"


async def test_member_rules_match_onboarding(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    headers, household = await _household(
        api,
        google,
        onboarding(diet="egg", members=[{"age_band": "18_59"}], consents=WITHOUT_HEALTH),
    )
    members = f"/v1/households/{household['id']}/members"
    member = f"{members}/{household['members'][0]['id']}"
    jain = {"age_band": "18_59", "fasting_rules": {"jain": True}}

    added_jain = await api.post(members, json=jain, headers=headers)
    made_jain = await api.patch(member, json={"fasting_rules": {"jain": True}}, headers=headers)
    added_tags = await api.post(
        members, json={"age_band": "18_59", "health_tags": ["pcos"]}, headers=headers
    )
    tagged = await api.patch(member, json={"health_tags": ["pcos"]}, headers=headers)

    for refused in (added_jain, made_jain):
        assert refused.status_code == 422
        assert refused.json()["code"] == "identity.diet_conflict"
    for refused in (added_tags, tagged):
        assert refused.status_code == 403
        assert refused.json()["code"] == "identity.health_consent_required"
    assert (await _me(api, headers))["version"] == 1


async def test_health_tags_can_be_edited_with_the_consent(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    member = f"/v1/households/{household['id']}/members/{household['members'][1]['id']}"

    response = await api.patch(member, json={"health_tags": ["low_oil"]}, headers=headers)

    assert response.status_code == 200
    assert response.json()["health_tags"] == ["low_oil"]


async def test_nobody_else_can_touch_the_members(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    _, household = await _household(api, google)
    other = await customer(api, google, 2)
    members = f"/v1/households/{household['id']}/members"
    member = f"{members}/{household['members'][0]['id']}"

    responses = [
        await api.post(members, json={"age_band": "18_59"}, headers=other),
        await api.patch(member, json={"sex": "f"}, headers=other),
        await api.delete(member, headers=other),
    ]

    assert [response.status_code for response in responses] == [404, 404, 404]
    assert {response.json()["code"] for response in responses} == {"identity.household_not_found"}


async def test_an_unknown_member_is_404(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    headers, household = await _household(api, google)
    path = f"/v1/households/{household['id']}/members/{uuid.uuid4()}"

    patched = await api.patch(path, json={"sex": "f"}, headers=headers)
    deleted = await api.delete(path, headers=headers)

    assert patched.status_code == deleted.status_code == 404
    assert patched.json()["code"] == "identity.member_not_found"


@pytest.mark.parametrize(
    "change", [{"age_band": None}, {"age_band": "adult"}, {"health_tags": ["x"]}, {"id": "1"}]
)
async def test_invalid_member_changes_are_422(
    clients: ClientFactory, google: FakeGoogle, change: dict[str, Any]
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    member = f"/v1/households/{household['id']}/members/{household['members'][0]['id']}"

    response = await api.patch(member, json=change, headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"


# --- address ----------------------------------------------------------------------------------


async def test_my_addresses_is_a_list_of_at_most_one(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)
    before = await api.get("/v1/addresses", headers=headers)
    household = (await onboard(api, headers)).json()

    after = await api.get("/v1/addresses", headers=headers)

    assert before.status_code == 200 and before.json() == []
    assert after.json() == [household["address"]]


async def test_editing_the_flat_and_pin_keeps_the_society(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    path = f"/v1/addresses/{household['address']['id']}"

    moved_flat = await api.patch(path, json={"flat": " c-1 ", "tower_id": "c"}, headers=headers)
    no_pin = await api.patch(path, json={"location": None}, headers=headers)

    assert moved_flat.status_code == 200, moved_flat.text
    assert (moved_flat.json()["tower_id"], moved_flat.json()["flat"]) == ("c", "C-1")
    assert moved_flat.headers["etag"] == '"2"'
    assert no_pin.json()["location"] is None and no_pin.headers["etag"] == '"3"'
    address = (await db_session.execute(select(Address))).scalar_one()
    assert address.location is None and address.h3_r9 is None
    assert (await _me(api, headers))["society_id"] == str(SEED_SOCIETY_ID)


async def test_only_a_real_change_bumps_the_version_and_a_flat_needs_no_checks(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    path = f"/v1/addresses/{household['address']['id']}"

    same = await api.patch(path, json={"flat": "a-1203", "tower_id": "a"}, headers=headers)
    flat_only = await api.patch(path, json={"flat": "A-1204"}, headers=headers)

    assert same.status_code == 200 and same.headers["etag"] == '"1"'
    assert flat_only.json()["flat"] == "A-1204" and flat_only.headers["etag"] == '"2"'


async def test_moving_to_another_society_moves_the_household(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers, household = await _household(api, google)
    unlock = "/v1/societies/{}/unlock"
    sunrise_before = (await api.get(unlock.format(SEED_SOCIETY_ID), headers=headers)).json()
    green_before = (await api.get(unlock.format(GREEN_VALLEY), headers=headers)).json()

    moved = await api.patch(
        f"/v1/addresses/{household['address']['id']}",
        json={"society_id": GREEN_VALLEY, "tower_id": "b", "flat": "B-9", "location": None},
        headers=headers,
    )

    assert moved.status_code == 200, moved.text
    assert moved.json()["society_id"] == GREEN_VALLEY
    me = await _me(api, headers)
    assert me["society_id"] == GREEN_VALLEY and me["hub_id"] == str(SEED_HUB_ID)
    assert me["address"] == moved.json()
    stored = (await db_session.execute(select(Household))).scalar_one()
    assert stored.society_id == uuid.UUID(GREEN_VALLEY)
    assert (await _changes(db_session, household["id"]))[-1] == ["address", "society_id"]
    sunrise_after = (await api.get(unlock.format(SEED_SOCIETY_ID), headers=headers)).json()
    green_after = (await api.get(unlock.format(GREEN_VALLEY), headers=headers)).json()
    assert sunrise_after["joined"] == sunrise_before["joined"] - 1
    assert green_after["joined"] == green_before["joined"] + 1


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"society_id": str(uuid.uuid4())}, "identity.society_unknown"),
        ({"tower_id": "z"}, "identity.tower_unknown"),
        # Green Valley has tower a, but the current pin is in Sunrise Towers.
        ({"society_id": GREEN_VALLEY}, "identity.address_outside_society"),
        ({"location": {"lat": 19.0, "lng": 73.1}}, "identity.address_outside_society"),
    ],
    ids=["society", "tower", "old-pin-outside-new-society", "pin-outside"],
)
async def test_address_rules_match_onboarding(
    clients: ClientFactory, google: FakeGoogle, change: dict[str, Any], code: str
) -> None:
    api = clients()
    headers, household = await _household(api, google)

    response = await api.patch(
        f"/v1/addresses/{household['address']['id']}", json=change, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == code
    assert (await _me(api, headers))["version"] == 1


async def test_nobody_else_can_edit_the_address(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    _, household = await _household(api, google)
    path = f"/v1/addresses/{household['address']['id']}"

    theirs = await api.patch(path, json={"flat": "X-1"}, headers=await customer(api, google, 2))
    unknown = await api.patch(
        f"/v1/addresses/{uuid.uuid4()}", json={"flat": "X-1"}, headers=await customer(api, google)
    )

    for response in (theirs, unknown):
        assert response.status_code == 404
        assert response.json()["code"] == "identity.address_not_found"


@pytest.mark.parametrize("change", [{"flat": None}, {"flat": "A#1"}, {"society_id": None}])
async def test_invalid_address_changes_are_422(
    clients: ClientFactory, google: FakeGoogle, change: dict[str, Any]
) -> None:
    api = clients()
    headers, household = await _household(api, google)

    response = await api.patch(
        f"/v1/addresses/{household['address']['id']}", json=change, headers=headers
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"
