"""POST /v1/admin/societies (M9 §3.1, §3.9): a prospect society made by hand on the same create
path as accepting a request: who may, which hub, the slug, the validation, replay and the audit."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_staff_societies import (
    NEHA,
    PRIYA,
    REQUESTS,
    SOCIETIES,
    VIKRAM,
    _as,
    _key,
    _manager_of,
    _request,
    code,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("kindness_world")]


async def test_a_hub_manager_adds_a_prospect_society_to_their_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    key = {**vikram, "Idempotency-Key": "create-1"}
    body = {
        "name": "  Lake  View ",
        "area": "Sector 7, Kharghar",
        "unlock_threshold": 25,
        "flats": 180,
        "towers": [{"label": "A wing"}, {"id": "b", "label": "B wing"}, {"label": "C wing"}],
    }

    created = await api.post(SOCIETIES, json=body, headers=key)
    replayed = await api.post(SOCIETIES, json=body, headers=key)
    changed = await api.post(SOCIETIES, json={**body, "flats": 181}, headers=key)
    missing_key = await api.post(SOCIETIES, json=body, headers=vikram)

    assert created.status_code == 201, created.text
    society = created.json()
    assert society["name"] == "Lake View" and society["slug"] == "lake-view"
    assert society["status"] == "prospect" and society["area"] == "Sector 7, Kharghar"
    assert society["hub"] == {"id": str(SEED_HUB_ID), "name": "Kharghar hub"}
    assert society["unlock_threshold"] == 25 and society["threshold_source"] == "hub"
    assert society["unlock_threshold_pinned"] is False and society["flats"] == 180
    assert society["towers"] == [
        {"id": "a", "label": "A wing"},
        {"id": "b", "label": "B wing"},
        {"id": "c", "label": "C wing"},
    ]
    assert society["boundary"] is None and society["households"] == 0
    assert society["live_at"] is None
    assert replayed.status_code == 201 and replayed.json() == society
    assert replayed.headers["idempotent-replayed"] == "true"
    assert code(changed) == (422, "request.idempotency_mismatch")
    assert missing_key.status_code == 400

    listed = await api.get(f"{SOCIETIES}?q=lake", headers=vikram)
    assert [s["id"] for s in listed.json()["items"]] == [society["id"]]
    stored = await db_session.get(Society, uuid.UUID(society["id"]))
    assert stored is not None and stored.data_source == "real"
    audited = (
        (
            await db_session.execute(
                select(AuditEntry).where(AuditEntry.action == "community.society_created")
            )
        )
        .scalars()
        .all()
    )
    assert len(audited) == 1 and audited[0].entity_id == society["id"]  # the replay logged nothing
    assert audited[0].actor_user_id is not None
    assert audited[0].after is not None and audited[0].after["slug"] == "lake-view"
    events = (
        await db_session.execute(
            select(OutboxEvent.event_type).where(OutboxEvent.event_type.like("community.%"))
        )
    ).scalars()
    assert list(events) == []


async def test_the_default_threshold_and_the_slug_suffix_for_a_taken_name(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)

    first = await api.post(SOCIETIES, json={"name": "Panvel Heights"}, headers={**neha, **_key()})
    second = await api.post(
        SOCIETIES,
        json={"name": "Panvel Heights", "hub_id": str(SEED_HUB_ID)},
        headers={**neha, **_key()},
    )

    assert first.status_code == 201, first.text
    assert first.json()["hub"] is None and first.json()["area"] is None  # an admin's: hub-less
    assert first.json()["unlock_threshold"] == 20 and first.json()["threshold_source"] == "default"
    assert first.json()["towers"] == [] and first.json()["flats"] is None
    assert first.json()["slug"] == "panvel-heights"
    assert second.json()["slug"] == "panvel-heights-2"
    assert second.json()["hub"]["id"] == str(SEED_HUB_ID)


async def test_the_hub_follows_the_callers_grants(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    other = await _manager_of(api, google, db_session, uuid.uuid4())

    foreign = await api.post(
        SOCIETIES,
        json={"name": "Elsewhere", "hub_id": str(SEED_HUB_ID)},
        headers={**other, **_key()},
    )
    unknown_hub = await api.post(
        SOCIETIES,
        json={"name": "Elsewhere", "hub_id": str(uuid.uuid4())},
        headers={**neha, **_key()},
    )
    own = await api.post(SOCIETIES, json={"name": "Mine"}, headers={**vikram, **_key()})

    assert code(foreign) == (403, "auth.forbidden")
    assert unknown_hub.status_code == 422 and unknown_hub.json()["errors"][0]["field"] == "hub_id"
    assert own.status_code == 201 and own.json()["hub"]["id"] == str(SEED_HUB_ID)
    elsewhere = (await db_session.execute(select(Society).where(Society.name == "Elsewhere"))).all()
    assert elsewhere == []  # the refusals created nothing


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({}, "name"),
        ({"name": "x"}, "name"),
        ({"name": " " * 5}, "name"),
        ({"name": "x" * 121}, "name"),
        ({"name": "Ok Heights", "area": "x" * 121}, "area"),
        ({"name": "Ok Heights", "unlock_threshold": 0}, "unlock_threshold"),
        ({"name": "Ok Heights", "unlock_threshold": 501}, "unlock_threshold"),
        ({"name": "Ok Heights", "flats": 0}, "flats"),
        ({"name": "Ok Heights", "towers": [{"label": ""}]}, "label"),
        ({"name": "Ok Heights", "towers": [{"id": "A B", "label": "A"}]}, "id"),
        (
            {
                "name": "Ok Heights",
                "towers": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}],
            },
            "unique",
        ),
        ({"name": "Ok Heights", "status": "live"}, "status"),
        ({"name": "Ok Heights", "boundary": None}, "boundary"),
    ],
)
async def test_invalid_bodies_are_422(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    body: dict[str, Any],
    field: str,
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)

    answer = await api.post(SOCIETIES, json=body, headers={**neha, **_key()})

    assert code(answer) == (422, "request.invalid"), answer.text
    assert field in str(answer.json()["errors"])
    made = (await db_session.execute(select(Society).where(Society.name == "Ok Heights"))).all()
    assert made == []


async def test_only_staff_add_societies(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    priya = await _as(api, google, PRIYA)

    customer = await api.post(SOCIETIES, json={"name": "Nope"}, headers={**priya, **_key()})
    anonymous = await api.post(SOCIETIES, json={"name": "Nope"}, headers=_key())

    assert code(customer) == (403, "auth.forbidden")
    assert anonymous.status_code == 401


async def test_accepting_a_request_still_creates_its_society_on_the_shared_path(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    request_id = await _request(db_session, "Shared Path Heights")
    api = clients()
    neha = await _as(api, google, NEHA)

    by_hand = await api.post(
        SOCIETIES, json={"name": "Shared Path Heights"}, headers={**neha, **_key()}
    )
    accepted = await api.post(
        f"{REQUESTS}/{request_id}/accept", json={}, headers={**neha, **_key()}
    )

    assert by_hand.json()["slug"] == "shared-path-heights"
    assert accepted.status_code == 201, accepted.text
    society = accepted.json()["society"]
    assert society["slug"] == "shared-path-heights-2" and society["status"] == "prospect"
    assert society["unlock_threshold"] == 20 and society["threshold_source"] == "default"
