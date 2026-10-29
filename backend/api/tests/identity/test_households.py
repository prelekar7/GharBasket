"""Households and consents (M1 §3.2): onboarding in one call, reads, edits and consent rules."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity import households as households_service
from gharbasket.modules.identity.models import Address, Consent, Household, HouseholdMember
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, seed_societies

from .. import seed_cache as cached
from .conftest import ClientFactory
from .fake_firebase import FakeGoogle
from .onboarding import ONBOARDING, count, customer, onboard, onboarding

pytestmark = [pytest.mark.anyio, pytest.mark.services]

SAI_DARSHAN = "0199a000-0000-7000-8000-00000000b006"  # seed: no towers, no boundary


@pytest.fixture(autouse=True)
async def societies(db_session: AsyncSession) -> None:
    await cached.seed_societies(db_session)


# --- onboarding -------------------------------------------------------------------------------


async def test_onboarding_creates_everything_in_one_call(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await customer(api, google)

    response = await onboard(api, headers)

    assert response.status_code == 201, response.text
    assert response.headers["etag"] == '"1"'
    body = response.json()
    assert body["version"] == 1 and body["size"] == 2
    assert body["society_id"] == str(SEED_SOCIETY_ID) and body["hub_id"] == str(SEED_HUB_ID)
    assert (body["autopilot_enabled"], body["auto_approve"]) == (True, False)
    assert body["shortage_preference"] == "substitute"
    assert body["address"]["flat"] == "A-1203"
    assert body["address"]["location"] == {"lat": 19.0396, "lng": 73.0676}
    assert body["address"]["household_id"] == body["id"]
    assert [member["health_tags"] for member in body["members"]] == [["diabetes"], []]
    assert body["members"][1]["fasting_rules"] == {
        "jain": False,
        "fasts": [],
        "no_onion_garlic_on_fast_days": False,
    }
    address = (await db_session.execute(select(Address))).scalar_one()
    assert address.h3_r9 is not None and len(address.h3_r9) == 15
    granted = (await db_session.execute(select(Consent.purpose))).scalars().all()
    assert sorted(granted) == ["health_personalisation", "personalisation", "service_delivery"]

    me = await api.get("/v1/households/me", headers=headers)
    assert me.json() == body and me.headers["etag"] == '"1"'


async def test_the_created_event_has_no_sensitive_details(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    body = (await onboard(api, await customer(api, google))).json()

    event = (
        await db_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "identity.household_created",
                OutboxEvent.payload["household_id"].astext == body["id"],
            )
        )
    ).scalar_one()

    assert event.payload == {
        "household_id": body["id"],
        "owner_user_id": event.payload["owner_user_id"],
        "society_id": str(SEED_SOCIETY_ID),
        "hub_id": str(SEED_HUB_ID),
        "size": 2,
        "budget_paise": 300_000,
        "data_source": "real",
    }


async def test_the_society_unlock_count_includes_the_new_household(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)
    unlock = f"/v1/societies/{SEED_SOCIETY_ID}/unlock"
    before = (await api.get(unlock, headers=headers)).json()["joined"]

    await onboard(api, headers)

    assert (await api.get(unlock, headers=headers)).json()["joined"] == before + 1


async def test_a_retry_with_the_same_key_replays_and_another_key_is_a_conflict(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**await customer(api, google), "Idempotency-Key": "onboard-1"}

    first = await api.post("/v1/households", json=ONBOARDING, headers=headers)
    retry = await api.post("/v1/households", json=ONBOARDING, headers=headers)
    second = await api.post(
        "/v1/households", json=ONBOARDING, headers={**headers, "Idempotency-Key": "onboard-2"}
    )

    assert retry.status_code == 201 and retry.json() == first.json()
    assert retry.headers["idempotent-replayed"] == "true"
    assert second.status_code == 409
    assert second.json()["code"] == "identity.household_exists"
    assert second.json()["household_id"] == first.json()["id"]
    assert await count(db_session, Household) == 1
    assert await count(db_session, HouseholdMember) == 2


async def test_a_failure_part_way_leaves_nothing_behind(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    headers = await customer(api, google)

    async def broken_publish(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("outbox down")

    monkeypatch.setattr(households_service, "publish", broken_publish)
    # The test client re-raises app errors (users get a 500); the request's session rolls back.
    with pytest.raises(RuntimeError, match="outbox down"):
        await onboard(api, headers)

    for model in (Household, HouseholdMember, Address, Consent):
        assert await count(db_session, model) == 0


async def test_a_concurrent_second_onboarding_is_a_clean_conflict(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    headers = await customer(api, google)
    first = (await onboard(api, headers)).json()
    # The second request's existence check runs before the first commits: it sees nothing.
    real_household_of = households_service.household_of
    calls = 0

    async def racing_household_of(*args: Any, **kwargs: Any) -> Household | None:
        nonlocal calls
        calls += 1
        return None if calls == 1 else await real_household_of(*args, **kwargs)

    monkeypatch.setattr(households_service, "household_of", racing_household_of)
    second = await onboard(api, headers)

    assert second.status_code == 409
    assert second.json()["household_id"] == first["id"]
    assert await count(db_session, Household) == 1


async def test_an_idempotency_key_is_required(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()

    response = await api.post(
        "/v1/households", json=ONBOARDING, headers=await customer(api, google)
    )

    assert response.status_code == 400
    assert response.json()["code"] == "request.idempotency_key_required"


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"society_id": str(uuid.uuid4())}, 422, "identity.society_unknown"),
        (
            {"address": {"tower_id": "z", "flat": "Z-1", "location": None}},
            422,
            "identity.tower_unknown",
        ),
        (
            {"address": {"tower_id": "a", "flat": "A-1", "location": {"lat": 19.0, "lng": 73.1}}},
            422,
            "identity.address_outside_society",
        ),
        (
            {"diet": "egg", "members": [{"age_band": "18_59", "fasting_rules": {"jain": True}}]},
            422,
            "identity.diet_conflict",
        ),
        (
            {"consents": [{"purpose": "service_delivery", "version": "v1", "granted": True}]},
            422,
            "identity.consent_required",
        ),
        (
            {"consents": [{"purpose": "service_delivery", "version": "v0", "granted": True}]},
            409,
            "identity.consent_version_stale",
        ),
        (
            {"consents": ONBOARDING["consents"][:2]},
            403,
            "identity.health_consent_required",
        ),
    ],
    ids=["society", "tower", "outside", "jain", "consent", "stale", "health"],
)
async def test_onboarding_rules(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    changes: dict[str, Any],
    status: int,
    code: str,
) -> None:
    api = clients()

    response = await onboard(api, await customer(api, google), onboarding(**changes))

    assert response.status_code == status, response.text
    assert response.json()["code"] == code
    assert await count(db_session, Household) == 0
    if code == "identity.consent_version_stale":
        assert response.json()["current_version"] == "v1"


async def test_a_society_without_a_boundary_accepts_any_pin_and_no_pin(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    await seed_societies(db_session)
    sai = await api.get(f"/v1/societies/{SAI_DARSHAN}", headers=await customer(api, google, 9))
    assert sai.json()["towers"] == []  # no towers yet, so the tower rule is checked elsewhere

    no_pin = onboarding(address={"tower_id": "b", "flat": "B-9", "location": None})
    response = await onboard(api, await customer(api, google), no_pin)

    assert response.status_code == 201, response.text
    assert response.json()["address"]["location"] is None
    address = (await db_session.execute(select(Address))).scalar_one()
    assert address.location is None and address.h3_r9 is None


@pytest.mark.parametrize(
    "changes",
    [
        {"members": []},
        {"members": [{"age_band": "18_59"}] * 13},
        {"budget_paise": 49_999},
        {"weekday_cook_minutes": 5},
        {"no_cook_days": [1, 1]},
        {"no_cook_days": [7]},
        {"cuisine_tags": ["maharashtrian", "maharashtrian"]},
        {"address": {"tower_id": "a", "flat": "A#12", "location": None}},
        {"address": {"tower_id": "a", "flat": "A-1", "location": {"lat": 0, "lng": 0}}},
        {"members": [{"age_band": "18_59", "health_tags": ["flu"]}]},
        {"consents": [ONBOARDING["consents"][0], ONBOARDING["consents"][0]]},
        {"shortage_preference": "never"},
        {"surprise": 1},
    ],
)
async def test_invalid_onboarding_is_422(
    clients: ClientFactory, google: FakeGoogle, changes: dict[str, Any]
) -> None:
    api = clients()

    response = await onboard(api, await customer(api, google), onboarding(**changes))

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"


# --- reading ----------------------------------------------------------------------------------


async def test_before_onboarding_my_household_is_not_found(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()

    response = await api.get("/v1/households/me", headers=await customer(api, google))

    assert response.status_code == 404
    assert response.json()["code"] == "identity.household_not_found"


async def test_only_the_owner_can_read_a_household(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    owner = await customer(api, google)
    household = (await onboard(api, owner)).json()
    path = f"/v1/households/{household['id']}"

    mine = await api.get(path, headers=owner)
    theirs = await api.get(path, headers=await customer(api, google, 2))
    anonymous = await api.get(path)

    assert mine.status_code == 200 and mine.json() == household
    assert theirs.status_code == 404
    assert theirs.json()["code"] == "identity.household_not_found"
    assert anonymous.status_code == 401


async def test_health_tags_are_hidden_without_the_consent(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await customer(api, google)
    await onboard(api, headers)
    # Tags left behind by some other path must still never be shown without the consent.
    active = (
        await db_session.execute(select(Consent).where(Consent.purpose == "health_personalisation"))
    ).scalar_one()
    await db_session.delete(active)
    await db_session.flush()

    me = (await api.get("/v1/households/me", headers=headers)).json()

    assert [member["health_tags"] for member in me["members"]] == [[], []]


# --- editing ----------------------------------------------------------------------------------


async def test_editing_needs_the_current_version(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await customer(api, google)
    path = f"/v1/households/{(await onboard(api, headers)).json()['id']}"
    change = {"weekday_cook_minutes": 45, "budget_paise": None}

    missing = await api.patch(path, json=change, headers=headers)
    edited = await api.patch(path, json=change, headers={**headers, "If-Match": '"1"'})
    stale = await api.patch(path, json={"diet": "egg"}, headers={**headers, "If-Match": '"1"'})
    garbage = await api.patch(path, json={"diet": "egg"}, headers={**headers, "If-Match": "abc"})

    assert missing.status_code == 428
    assert missing.json()["code"] == "request.precondition_required"
    assert edited.status_code == 200, edited.text
    assert edited.headers["etag"] == '"2"'
    assert (edited.json()["version"], edited.json()["weekday_cook_minutes"]) == (2, 45)
    assert edited.json()["budget_paise"] is None
    for conflict in (stale, garbage):
        assert conflict.status_code == 412
        assert conflict.json()["code"] == "identity.version_conflict"
    event = (
        await db_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "identity.household_updated",
                OutboxEvent.payload["household_id"].astext == path.rsplit("/", 1)[-1],
            )
        )
    ).scalar_one()
    assert event.payload["version"] == 2
    assert event.payload["changed"] == ["budget_paise", "weekday_cook_minutes"]


async def test_an_edit_that_changes_nothing_keeps_the_version(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)
    path = f"/v1/households/{(await onboard(api, headers)).json()['id']}"

    same = await api.patch(path, json={"diet": "veg"}, headers={**headers, "If-Match": '"1"'})

    assert same.status_code == 200 and same.json()["version"] == 1


async def test_a_jain_household_cannot_switch_to_eggs(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = await customer(api, google)
    jain = onboarding(members=[{"age_band": "18_59", "fasting_rules": {"jain": True}}])
    path = f"/v1/households/{(await onboard(api, headers, jain)).json()['id']}"

    response = await api.patch(path, json={"diet": "egg"}, headers={**headers, "If-Match": '"1"'})

    assert response.status_code == 422
    assert response.json()["code"] == "identity.diet_conflict"


@pytest.mark.parametrize(
    "change",
    [
        {"diet": None},
        {"budget_paise": 100},
        {"size": 3},
        {"no_cook_days": [8]},
        {"shortage_preference": None},
        {"shortage_preference": "never"},
    ],
)
async def test_invalid_edits_are_422(
    clients: ClientFactory, google: FakeGoogle, change: dict[str, Any]
) -> None:
    api = clients()
    headers = await customer(api, google)
    path = f"/v1/households/{(await onboard(api, headers)).json()['id']}"

    response = await api.patch(path, json=change, headers={**headers, "If-Match": '"1"'})

    assert response.status_code == 422


async def test_nobody_else_can_edit_a_household(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    path = f"/v1/households/{(await onboard(api, await customer(api, google))).json()['id']}"

    response = await api.patch(
        path,
        json={"diet": "egg"},
        headers={**await customer(api, google, 2), "If-Match": '"1"'},
    )

    assert response.status_code == 404


# --- shortage preference (§3.4) -----------------------------------------------------------------


async def test_a_household_can_choose_a_refund_instead_of_a_substitute(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await customer(api, google)
    household_id = uuid.UUID((await onboard(api, headers)).json()["id"])
    path = f"/v1/households/{household_id}"
    assert await identity_api.shortage_preferences(db_session, [household_id]) == {
        household_id: "substitute"
    }
    await db_session.commit()  # the refused request below rolls back the shared session

    missing = await api.patch(path, json={"shortage_preference": "refund"}, headers=headers)
    assert missing.status_code == 428
    edited = await api.patch(
        path, json={"shortage_preference": "refund"}, headers={**headers, "If-Match": '"1"'}
    )

    assert edited.status_code == 200, edited.text
    assert edited.headers["etag"] == '"2"'
    assert (edited.json()["version"], edited.json()["shortage_preference"]) == (2, "refund")
    assert (await api.get("/v1/households/me", headers=headers)).json() == edited.json()
    event = (
        await db_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == "identity.household_updated",
                OutboxEvent.payload["household_id"].astext == str(household_id),
            )
        )
    ).scalar_one()
    assert (event.payload["version"], event.payload["changed"]) == (2, ["shortage_preference"])
    assert await identity_api.shortage_preferences(db_session, [household_id]) == {
        household_id: "refund"
    }

    back = await api.patch(
        path, json={"shortage_preference": "substitute"}, headers={**headers, "If-Match": '"2"'}
    )
    assert (back.json()["version"], back.json()["shortage_preference"]) == (3, "substitute")


async def test_onboarding_can_choose_a_refund_and_unknown_households_read_as_substitute(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()

    created = await onboard(
        api, await customer(api, google), onboarding(shortage_preference="refund")
    )

    assert created.status_code == 201, created.text
    assert created.json()["shortage_preference"] == "refund"
    household_id, stranger = uuid.UUID(created.json()["id"]), uuid.uuid4()
    assert await identity_api.shortage_preferences(db_session, [household_id, stranger]) == {
        household_id: "refund",
        stranger: "substitute",
    }
    assert await identity_api.shortage_preferences(db_session, []) == {}


# --- consents ---------------------------------------------------------------------------------


async def test_consents_list_every_purpose(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    headers = await customer(api, google)
    await onboard(api, headers)

    consents = (await api.get("/v1/consents", headers=headers)).json()

    granted = {consent["purpose"]: consent["granted"] for consent in consents}
    assert granted == {
        "service_delivery": True,
        "personalisation": True,
        "health_personalisation": True,
        "marketing": False,
        "research_anonymised": False,
        "whatsapp": False,
    }
    never = next(consent for consent in consents if consent["purpose"] == "marketing")
    assert never == {
        "purpose": "marketing",
        "version": "v1",
        "granted": False,
        "granted_at": None,
        "revoked_at": None,
    }


async def test_withdrawing_health_consent_erases_every_health_tag(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await customer(api, google)
    household = (await onboard(api, headers)).json()

    withdrawn = await api.post(
        "/v1/consents",
        json={"purpose": "health_personalisation", "version": "v1", "granted": False},
        headers=headers,
    )

    assert withdrawn.status_code == 200
    assert withdrawn.json()["granted"] is False and withdrawn.json()["revoked_at"] is not None
    tags = (await db_session.execute(select(HouseholdMember.health_tags))).scalars().all()
    assert all(member_tags == [] for member_tags in tags)
    audit = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "identity.health_tags_erased")
        )
    ).scalar_one()
    assert audit.entity_id == household["id"]
    assert audit.before == {"members_with_tags": 1}


async def test_granting_twice_keeps_one_active_consent_and_regranting_after_a_withdrawal_works(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = await customer(api, google)
    marketing = {"purpose": "marketing", "version": "v1"}

    for granted in (True, True, False, False, True):
        response = await api.post(
            "/v1/consents", json={**marketing, "granted": granted}, headers=headers
        )
        assert response.status_code == 200
        assert response.json()["granted"] is granted

    rows = (
        (await db_session.execute(select(Consent).where(Consent.purpose == "marketing")))
        .scalars()
        .all()
    )
    assert len(rows) == 2
    assert sum(row.revoked_at is None for row in rows) == 1


@pytest.mark.parametrize("granted", [0, 1, "true", None])
async def test_consent_must_be_an_explicit_true_or_false(
    clients: ClientFactory, google: FakeGoogle, granted: Any
) -> None:
    api = clients()

    response = await api.post(
        "/v1/consents",
        json={"purpose": "marketing", "version": "v1", "granted": granted},
        headers=await customer(api, google),
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"


async def test_an_old_notice_version_is_refused(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()

    response = await api.post(
        "/v1/consents",
        json={"purpose": "marketing", "version": "v0", "granted": True},
        headers=await customer(api, google),
    )

    assert response.status_code == 409
    assert response.json() | {"current_version": "v1"} == response.json()
