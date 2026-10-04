"""POST /v1/society-requests (M9 §3.1, US-8): a resident asks for a society that isn't listed."""

from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import SocietyRequest

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import customer, rider_only

pytestmark = [pytest.mark.anyio, pytest.mark.services]

BODY: dict[str, Any] = {
    "name": "Panvel Heights",
    "area": "New Panvel",
    "location": {"lat": 18.99, "lng": 73.11},
    "note": "Near the station",
}


async def _stored(db: AsyncSession, request_id: str) -> tuple[SocietyRequest, Any]:
    row = (
        await db.execute(
            select(SocietyRequest, func.ST_AsText(SocietyRequest.location)).where(
                SocietyRequest.id == request_id
            )
        )
    ).one()
    return row[0], row[1]


async def test_a_request_is_stored_once_and_replays_with_the_same_key(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**await customer(api, google), "Idempotency-Key": "society-req-1"}

    created = await api.post("/v1/society-requests", json=BODY, headers=headers)
    replayed = await api.post("/v1/society-requests", json=BODY, headers=headers)

    assert created.status_code == replayed.status_code == 201
    assert set(created.json()) == {"id", "status", "created_at"}
    assert created.json()["status"] == "received"
    assert replayed.json() == created.json()
    assert replayed.headers["idempotent-replayed"] == "true"
    count = await db_session.execute(select(func.count()).select_from(SocietyRequest))
    assert count.scalar_one() == 1
    stored, location = await _stored(db_session, created.json()["id"])
    assert (stored.name, stored.area, stored.note) == ("Panvel Heights", "New Panvel", BODY["note"])
    assert location == "POINT(73.11 18.99)"
    assert stored.data_source == "real"


async def test_without_a_pin_the_location_stays_empty(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**await customer(api, google), "Idempotency-Key": "no-pin"}

    created = await api.post(
        "/v1/society-requests",
        json={"name": "  Panvel   Heights ", "location": None},
        headers=headers,
    )

    assert created.status_code == 201, created.text
    stored, location = await _stored(db_session, created.json()["id"])
    assert stored.name == "Panvel Heights"
    assert location is None and stored.area is None and stored.note is None


async def test_the_same_key_with_a_different_body_is_refused(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    headers = {**await customer(api, google), "Idempotency-Key": "k"}

    await api.post("/v1/society-requests", json=BODY, headers=headers)
    changed = await api.post(
        "/v1/society-requests", json={**BODY, "name": "Other"}, headers=headers
    )

    assert changed.status_code == 422
    assert changed.json()["code"] == "request.idempotency_mismatch"


async def test_an_idempotency_key_is_required(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()

    response = await api.post(
        "/v1/society-requests", json=BODY, headers=await customer(api, google)
    )

    assert response.status_code == 400
    assert response.json()["code"] == "request.idempotency_key_required"


@pytest.mark.parametrize(
    "change",
    [
        {"name": "x"},
        {"name": "   "},
        {"name": "n" * 121},
        {"note": "n" * 501},
        {"location": {"lat": 0, "lng": 0}},
        {"location": {"lat": 51.5, "lng": -0.12}},
        {"surprise": True},
    ],
)
async def test_invalid_requests_are_422(
    clients: ClientFactory, google: FakeGoogle, change: dict[str, Any]
) -> None:
    api = clients()
    headers = {**await customer(api, google), "Idempotency-Key": "bad"}

    response = await api.post("/v1/society-requests", json={**BODY, **change}, headers=headers)

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"


async def test_only_customers_can_request_a_society(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    headers = {**await rider_only(api, google, db_session), "Idempotency-Key": "rider"}

    response = await api.post("/v1/society-requests", json=BODY, headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "auth.forbidden"


async def test_a_user_can_send_five_requests_a_day(
    clients: ClientFactory, google: FakeGoogle, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    signed_in = await customer(api, google)
    other = await customer(api, google, n=2)

    statuses = [
        (
            await api.post(
                "/v1/society-requests", json=BODY, headers={**signed_in, "Idempotency-Key": f"r{i}"}
            )
        ).status_code
        for i in range(6)
    ]
    limited = await api.post(
        "/v1/society-requests", json=BODY, headers={**signed_in, "Idempotency-Key": "r6"}
    )
    replay = await api.post(
        "/v1/society-requests", json=BODY, headers={**signed_in, "Idempotency-Key": "r0"}
    )
    someone_else = await api.post(
        "/v1/society-requests", json=BODY, headers={**other, "Idempotency-Key": "r0"}
    )

    assert statuses == [201, 201, 201, 201, 201, 429]
    assert limited.json()["code"] == "community.request_rate_limited"
    assert 0 < int(limited.headers["retry-after"]) <= 24 * 60 * 60
    # A retry of an earlier request still gets its answer, and the limit is per person.
    assert replay.status_code == someone_else.status_code == 201
