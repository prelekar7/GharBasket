"""The staff society list and the society request review (M9 §3.9): who sees which societies, the
order and fields of the list, the queue of requests, accepting one into a prospect society,
marking a duplicate and undoing it, and that nothing names a requester."""

import uuid
from typing import Any

import httpx2
import pytest
from geoalchemy2 import WKTElement
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society, SocietyRequest
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("kindness_world")]

PRIYA = "+919800001001"  # customer, Sunrise Towers
VIKRAM = "+919800001008"  # hub manager of the seed hub
NEHA = "+919800001011"  # admin
SOCIETIES = "/v1/admin/societies"
REQUESTS = "/v1/admin/society-requests"
SEED_NAMES = [
    "Krishna Kunj",
    "Sunrise Towers",
    "Green Valley Heights",
    "Shree Ganesh CHS",
    "Panchavati Residency",
]  # live, unlocking, prospect, each by name: the seed hub's five
HUBLESS = "Sai Darshan Apartments"


async def _as(api: httpx2.AsyncClient, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _manager_of(
    api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession, hub_id: uuid.UUID
) -> dict[str, str]:
    """Headers of a hub manager of another hub (the role is added, then a fresh token)."""
    signed = await sign_in(api, google, phone_number="+919800000777", sub="other-manager")
    user = await db.get(User, uuid.UUID(signed["user"]["id"]))
    assert user is not None
    db.add(UserRole(user_id=user.id, role="hub_manager", scope_type="hub", scope_id=hub_id))
    await db.commit()
    refreshed = await api.post("/v1/auth/refresh", json={"refresh_token": signed["refresh_token"]})
    return {**bearer(refreshed.json()["access_token"]), "X-Active-Role": f"hub_manager:{hub_id}"}


async def _request(db: AsyncSession, name: str = "Panvel Heights", **fields: Any) -> uuid.UUID:
    user = (await db.execute(select(User.id).limit(1))).scalar_one()
    made = SocietyRequest(
        user_id=user,
        name=name,
        area=fields.pop("area", "New Panvel"),
        location=fields.pop("location", WKTElement("POINT(73.11 18.99)", srid=4326)),
        note=fields.pop("note", "Near the station"),
        **fields,
    )
    db.add(made)
    await db.flush()
    made_id = made.id
    await db.commit()
    return made_id


def _key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


# --- the staff list ---------------------------------------------------------------------------


async def test_a_hub_manager_sees_their_hubs_societies_and_an_admin_every_one(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)

    manager = await api.get(SOCIETIES, headers=vikram)
    admin = await api.get(SOCIETIES, headers=neha)
    manager_unassigned = await api.get(f"{SOCIETIES}?with_unassigned=true", headers=vikram)
    admin_hub = await api.get(f"{SOCIETIES}?hub_id={SEED_HUB_ID}", headers=neha)
    admin_hub_plus = await api.get(
        f"{SOCIETIES}?hub_id={SEED_HUB_ID}&with_unassigned=true", headers=neha
    )

    assert manager.status_code == 200, manager.text
    assert manager.headers["Cache-Control"] == "no-store"
    assert [s["name"] for s in manager.json()["items"]] == SEED_NAMES
    assert [s["name"] for s in admin.json()["items"]] == [*SEED_NAMES, HUBLESS]
    assert [s["name"] for s in manager_unassigned.json()["items"]] == SEED_NAMES  # ignored
    assert [s["name"] for s in admin_hub.json()["items"]] == SEED_NAMES
    assert [s["name"] for s in admin_hub_plus.json()["items"]] == [*SEED_NAMES, HUBLESS]
    assert manager.json()["next_cursor"] is None


async def test_the_list_carries_the_hub_households_threshold_towers_and_boundary(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha = await _as(api, google, NEHA)

    answer = await api.get(SOCIETIES, headers=neha)

    by_name = {s["name"]: s for s in answer.json()["items"]}
    sunrise = by_name["Sunrise Towers"]
    assert sunrise["id"] == str(SEED_SOCIETY_ID)
    assert sunrise["slug"] == "sunrise-towers-kharghar" and sunrise["status"] == "live"
    assert sunrise["area"] == "Sector 20, Kharghar"
    assert sunrise["hub"] == {"id": str(SEED_HUB_ID), "name": "Kharghar hub"}
    assert sunrise["households"] >= 1
    assert sunrise["unlock_threshold"] == 20 and sunrise["unlock_threshold_pinned"] is False
    assert sunrise["threshold_source"] == "default"
    assert sunrise["live_at"] is not None and sunrise["flats"] == 240
    assert [t["id"] for t in sunrise["towers"]] == ["a", "b", "c"]
    assert sunrise["boundary"]["type"] == "Polygon"
    ring = sunrise["boundary"]["coordinates"][0]
    assert ring[0] == ring[-1] and all(len(point) == 2 for point in ring)  # [lng, lat], closed
    assert {"created_at", "updated_at"} <= set(sunrise)
    assert "distance_m" not in sunrise
    hubless = by_name[HUBLESS]
    assert hubless["hub"] is None and hubless["boundary"] is None and hubless["towers"] == []
    assert hubless["households"] == 0 and hubless["flats"] is None
    # No captain, requester or phone anywhere in the list.
    text = answer.text
    assert "captain" not in text and "phone" not in text and "user_id" not in text


async def test_the_list_filters_by_name_status_and_hub_and_pages_in_order(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    other_hub = await _manager_of(api, google, db_session, uuid.uuid4())

    by_name = await api.get(f"{SOCIETIES}?q=sunrise", headers=neha)
    by_area = await api.get(f"{SOCIETIES}?q=kharghar&status=unlocking", headers=neha)
    prospects = await api.get(f"{SOCIETIES}?status=prospect", headers=vikram)
    seen: list[str] = []
    cursor = ""
    while True:
        page = (await api.get(f"{SOCIETIES}?limit=2{cursor}", headers=neha)).json()
        seen += [s["name"] for s in page["items"]]
        if page["next_cursor"] is None:
            break
        cursor = f"&cursor={page['next_cursor']}"
    bad_cursor = await api.get(f"{SOCIETIES}?cursor=nonsense", headers=neha)
    bad_status = await api.get(f"{SOCIETIES}?status=closed", headers=neha)
    foreign = await api.get(f"{SOCIETIES}?hub_id={uuid.uuid4()}", headers=vikram)
    empty = await api.get(SOCIETIES, headers=other_hub)

    assert [s["name"] for s in by_name.json()["items"]] == ["Sunrise Towers"]
    assert [s["name"] for s in by_area.json()["items"]] == [
        "Green Valley Heights",
        "Shree Ganesh CHS",
    ]
    assert [s["name"] for s in prospects.json()["items"]] == ["Panchavati Residency"]
    assert seen == [*SEED_NAMES, HUBLESS]
    assert code(bad_cursor)[0] == 422 and bad_status.status_code == 422
    assert code(foreign) == (403, "auth.forbidden")
    assert empty.status_code == 200 and empty.json()["items"] == []  # their hub has no societies


async def test_only_staff_read_the_society_list_and_the_queue(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya = await _as(api, google, PRIYA)
    request_id = await _request(db_session)

    answers = [
        await api.get(SOCIETIES, headers=priya),
        await api.get(REQUESTS, headers=priya),
        await api.post(f"{REQUESTS}/{request_id}/accept", json={}, headers={**priya, **_key()}),
        await api.post(
            f"{REQUESTS}/{request_id}/duplicate",
            json={"society_id": str(SEED_SOCIETY_ID)},
            headers={**priya, **_key()},
        ),
        await api.patch(f"{REQUESTS}/{request_id}", json={"status": "received"}, headers=priya),
        await api.get(SOCIETIES),
    ]

    assert [a.status_code for a in answers] == [403, 403, 403, 403, 403, 401]


# --- the request queue ------------------------------------------------------------------------


async def test_the_queue_is_newest_first_filters_by_status_and_names_no_requester(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    older = await _request(db_session, "Older Heights")
    newer = await _request(db_session, "Newer Heights", location=None, area=None, note=None)
    done = await _request(db_session, "Done Heights", status="rejected")
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)

    manager = await api.get(REQUESTS, headers=vikram)
    admin = await api.get(f"{REQUESTS}?status=received", headers=neha)
    rejected = await api.get(f"{REQUESTS}?status=rejected", headers=neha)
    paged = [
        (await api.get(f"{REQUESTS}?limit=1", headers=neha)).json(),
    ]
    second = (
        await api.get(f"{REQUESTS}?limit=1&cursor={paged[0]['next_cursor']}", headers=neha)
    ).json()
    bad = await api.get(f"{REQUESTS}?status=pending", headers=neha)

    assert manager.status_code == 200, manager.text
    assert manager.headers["Cache-Control"] == "no-store"
    assert [r["id"] for r in manager.json()["items"]] == [str(done), str(newer), str(older)]
    assert [r["id"] for r in admin.json()["items"]] == [str(newer), str(older)]
    assert [r["id"] for r in rejected.json()["items"]] == [str(done)]
    first = next(r for r in manager.json()["items"] if r["id"] == str(older))
    assert first == {
        "id": str(older),
        "name": "Older Heights",
        "area": "New Panvel",
        "location": {"lat": 18.99, "lng": 73.11},
        "note": "Near the station",
        "status": "received",
        "society_id": None,
        "duplicate_of": None,
        "created_at": first["created_at"],
        "reviewed_at": None,
    }
    blank = next(r for r in manager.json()["items"] if r["id"] == str(newer))
    assert blank["location"] is None and blank["area"] is None and blank["note"] is None
    assert [r["id"] for r in paged[0]["items"]] == [str(done)]
    assert [r["id"] for r in second["items"]] == [str(newer)]
    assert bad.status_code == 422


# --- accept -----------------------------------------------------------------------------------


async def test_a_hub_manager_accepts_a_request_into_a_prospect_society_of_their_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    request_id = await _request(db_session)
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    key = {**vikram, "Idempotency-Key": "accept-1"}

    accepted = await api.post(f"{REQUESTS}/{request_id}/accept", json={}, headers=key)
    replayed = await api.post(f"{REQUESTS}/{request_id}/accept", json={}, headers=key)
    again = await api.post(f"{REQUESTS}/{request_id}/accept", json={}, headers={**vikram, **_key()})
    missing_key = await api.post(f"{REQUESTS}/{request_id}/accept", json={}, headers=vikram)

    assert accepted.status_code == 201, accepted.text
    body = accepted.json()
    society = body["society"]
    assert body["request"]["status"] == "accepted" and body["request"]["reviewed_at"] is not None
    assert (
        body["request"]["society_id"] == society["id"] and body["request"]["duplicate_of"] is None
    )
    assert society["name"] == "Panvel Heights" and society["area"] == "New Panvel"
    assert society["slug"] == "panvel-heights" and society["status"] == "prospect"
    assert society["hub"] == {"id": str(SEED_HUB_ID), "name": "Kharghar hub"}
    assert society["unlock_threshold"] == 20 and society["threshold_source"] == "default"
    assert society["boundary"] is None and society["towers"] == [] and society["flats"] is None
    assert society["households"] == 0 and society["live_at"] is None
    assert replayed.status_code == 201 and replayed.json() == body
    assert replayed.headers["idempotent-replayed"] == "true"
    assert code(again) == (409, "community.request_closed")
    assert missing_key.status_code == 400

    stored = await db_session.get(Society, uuid.UUID(society["id"]))
    assert stored is not None and stored.hub_id == SEED_HUB_ID
    audited = (
        (
            await db_session.execute(
                select(AuditEntry).where(AuditEntry.action == "community.request_reviewed")
            )
        )
        .scalars()
        .all()
    )
    assert len(audited) == 1 and audited[0].entity_id == str(request_id)
    assert audited[0].after == {"outcome": "accepted", "society_id": society["id"]}
    assert audited[0].actor_user_id is not None
    events = (
        (
            await db_session.execute(
                select(OutboxEvent.event_type).where(OutboxEvent.event_type.like("community.%"))
            )
        )
        .scalars()
        .all()
    )
    assert events == []  # accepting publishes nothing


async def test_accepting_picks_the_hub_by_role_and_a_taken_name_gets_a_suffix(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    first, second, third = [await _request(db_session) for _ in range(3)]
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    other = await _manager_of(api, google, db_session, uuid.uuid4())

    admin_no_hub = await api.post(f"{REQUESTS}/{first}/accept", json={}, headers={**neha, **_key()})
    admin_with_hub = await api.post(
        f"{REQUESTS}/{second}/accept",
        json={"hub_id": str(SEED_HUB_ID)},
        headers={**neha, **_key()},
    )
    foreign = await api.post(
        f"{REQUESTS}/{third}/accept",
        json={"hub_id": str(SEED_HUB_ID)},
        headers={**other, **_key()},
    )
    unknown_hub = await api.post(
        f"{REQUESTS}/{third}/accept", json={"hub_id": str(uuid.uuid4())}, headers={**neha, **_key()}
    )
    no_body = await api.post(f"{REQUESTS}/{third}/accept", headers={**vikram, **_key()})

    assert admin_no_hub.status_code == 201, admin_no_hub.text
    assert admin_no_hub.json()["society"]["hub"] is None  # an admin's stays hub-less
    assert admin_no_hub.json()["society"]["slug"] == "panvel-heights"
    assert admin_with_hub.json()["society"]["hub"]["id"] == str(SEED_HUB_ID)
    assert admin_with_hub.json()["society"]["slug"] == "panvel-heights-2"
    assert code(foreign) == (403, "auth.forbidden")
    assert unknown_hub.status_code == 422
    assert unknown_hub.json()["errors"][0]["field"] == "hub_id"
    assert no_body.status_code == 201 and no_body.json()["society"]["slug"] == "panvel-heights-3"
    # The refusals changed nothing: the third request was accepted once, by the last call.
    queue = await api.get(f"{REQUESTS}?status=accepted", headers=neha)
    assert len(queue.json()["items"]) == 3
    unknown = await api.post(
        f"{REQUESTS}/{uuid.uuid4()}/accept", json={}, headers={**neha, **_key()}
    )
    assert code(unknown) == (404, "community.request_not_found")


# --- duplicate and its undo -------------------------------------------------------------------


async def test_a_request_is_marked_a_duplicate_and_the_mark_is_undone(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    request_id = await _request(db_session)
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    url = f"{REQUESTS}/{request_id}"
    key = {**vikram, "Idempotency-Key": "dup-1"}

    marked = await api.post(
        f"{url}/duplicate", json={"society_id": str(SEED_SOCIETY_ID)}, headers=key
    )
    replayed = await api.post(
        f"{url}/duplicate", json={"society_id": str(SEED_SOCIETY_ID)}, headers=key
    )
    closed = await api.post(
        f"{url}/duplicate", json={"society_id": str(SEED_SOCIETY_ID)}, headers={**vikram, **_key()}
    )
    accept_closed = await api.post(f"{url}/accept", json={}, headers={**vikram, **_key()})
    undone = await api.patch(url, json={"status": "received"}, headers=vikram)
    undone_twice = await api.patch(url, json={"status": "received"}, headers=vikram)
    again = await api.post(
        f"{url}/duplicate", json={"society_id": str(SEED_SOCIETY_ID)}, headers={**vikram, **_key()}
    )

    assert marked.status_code == 200, marked.text
    one = marked.json()
    assert one["status"] == "duplicate" and one["duplicate_of"] == str(SEED_SOCIETY_ID)
    assert one["society_id"] is None and one["reviewed_at"] is not None
    assert replayed.json() == one and replayed.headers["idempotent-replayed"] == "true"
    assert code(closed) == (409, "community.request_closed")
    assert code(accept_closed) == (409, "community.request_closed")
    assert undone.status_code == 200, undone.text
    assert undone.json()["status"] == "received" and undone.json()["duplicate_of"] is None
    assert undone.json()["reviewed_at"] is None and undone.json()["society_id"] is None
    assert code(undone_twice) == (409, "community.request_closed")
    assert again.status_code == 200  # a received request can be marked again
    audited = (
        (
            await db_session.execute(
                select(AuditEntry.after)
                .where(AuditEntry.action == "community.request_reviewed")
                .order_by(AuditEntry.id)
            )
        )
        .scalars()
        .all()
    )
    assert [a["outcome"] for a in audited] == ["duplicate", "received", "duplicate"]


async def test_a_duplicate_needs_a_listed_society_and_the_undo_takes_only_received(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    request_id = await _request(db_session)
    accepted = await _request(db_session, "Taken Heights", status="accepted")
    api = clients()
    neha = await _as(api, google, NEHA)
    url = f"{REQUESTS}/{request_id}"

    missing = await api.post(f"{url}/duplicate", json={}, headers={**neha, **_key()})
    unknown = await api.post(
        f"{url}/duplicate", json={"society_id": str(uuid.uuid4())}, headers={**neha, **_key()}
    )
    not_a_uuid = await api.post(
        f"{url}/duplicate", json={"society_id": "x"}, headers={**neha, **_key()}
    )
    no_such_request = await api.post(
        f"{REQUESTS}/{uuid.uuid4()}/duplicate",
        json={"society_id": str(SEED_SOCIETY_ID)},
        headers={**neha, **_key()},
    )
    wrong_status = await api.patch(url, json={"status": "accepted"}, headers=neha)
    extra = await api.patch(url, json={"status": "received", "name": "x"}, headers=neha)
    empty = await api.patch(url, json={}, headers=neha)
    not_a_duplicate = await api.patch(url, json={"status": "received"}, headers=neha)
    on_accepted = await api.patch(
        f"{REQUESTS}/{accepted}", json={"status": "received"}, headers=neha
    )
    unknown_patch = await api.patch(
        f"{REQUESTS}/{uuid.uuid4()}", json={"status": "received"}, headers=neha
    )

    for refused in (missing, unknown):
        assert code(refused) == (422, "request.invalid")
        assert refused.json()["errors"][0]["field"] == "society_id"
    assert not_a_uuid.status_code == 422
    assert code(no_such_request) == (404, "community.request_not_found")
    assert [a.status_code for a in (wrong_status, extra, empty)] == [422, 422, 422]
    assert code(not_a_duplicate) == (409, "community.request_closed")
    assert code(on_accepted) == (409, "community.request_closed")
    assert code(unknown_patch) == (404, "community.request_not_found")
    stored = await db_session.get(SocietyRequest, request_id, populate_existing=True)
    assert stored is not None and stored.status == "received" and stored.society_id is None
