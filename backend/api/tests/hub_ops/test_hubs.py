"""Hubs and service areas (M6 §3.1): admin create and edit, validation, ETag/If-Match, who sees
which hub, the events, the facade and the seed hub."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import PriceList
from gharbasket.modules.community.models import Society
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.models import Hub, Vendor
from gharbasket.modules.identity.models import Household, UserRole
from gharbasket.modules.orders.models import Slot
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    seed_catalog,
    seed_delivery,
    seed_households,
    seed_hubs,
    seed_societies,
)

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, VIKRAM, as_user, hub_body, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("people")]

SQUARE = [[73.0, 19.07], [73.01, 19.07], [73.01, 19.08], [73.0, 19.08], [73.0, 19.07]]


def area(*rings: list[list[float]]) -> dict[str, Any]:
    return {"type": "Polygon", "coordinates": list(rings)}


async def events(db: AsyncSession, event_type: str, hub_id: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload).where(
            OutboxEvent.event_type == event_type, OutboxEvent.payload["hub_id"].astext == hub_id
        )
    )
    return list(rows.scalars())


async def test_an_admin_creates_a_hub_once_per_key(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    headers = {**admin, **key()}

    body = hub_body(service_area=area(SQUARE), timings={"dispatch": "07:00"})
    created = await api.post("/v1/admin/hubs", json=body, headers=headers)
    replayed = await api.post("/v1/admin/hubs", json=body, headers=headers)

    assert created.status_code == 201, created.text
    assert created.headers["ETag"] == '"1"'
    hub = created.json()
    assert replayed.json() == hub
    assert hub["status"] == "active" and hub["version"] == 1
    assert hub["location"] == {"lat": 19.0771, "lng": 72.9986}
    assert hub["service_area"] == area(SQUARE)
    assert hub["timings"] == {
        "receiving": "04:00",
        "packing": "05:00",
        "dispatch": "07:00",
        "surplus": "12:30",
    }
    assert hub["procurement_buffer_pct"] == 10
    assert hub["pre_live_box_fee_paise"] == 3_000  # ₹30 a box before a society is live
    assert await events(db_session, "hub_ops.hub_created", hub["id"]) == [
        {"hub_id": hub["id"], "slug": "vashi", "name": "Vashi hub"}
    ]
    read = await api.get(f"/v1/hubs/{hub['id']}", headers=admin)
    assert read.json() == hub
    assert read.headers["ETag"] == '"1"'
    assert read.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"slug": "kharghar"}, 409, "hub_ops.slug_taken"),
        ({"slug": "Vashi"}, 422, "request.invalid"),
        ({"name": "  x  "}, 422, "request.invalid"),
        ({"procurement_buffer_pct": 51}, 422, "request.invalid"),
        ({"pre_live_box_fee_paise": -1}, 422, "request.invalid"),
        ({"pre_live_box_fee_paise": 10_001}, 422, "request.invalid"),
        ({"service_area": area([*SQUARE[:-1], [73.0, 19.071]])}, 422, "hub_ops.area_invalid"),
        ({"service_area": area(SQUARE, SQUARE)}, 422, "hub_ops.area_invalid"),
        ({"service_area": area([*SQUARE[:2], SQUARE[-1]])}, 422, "hub_ops.area_invalid"),
        ({"service_area": area([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 0.0]])}, 422, None),
        # A bow tie: the edges cross.
        (
            {
                "service_area": area(
                    [[73.0, 19.0], [73.1, 19.1], [73.1, 19.0], [73.0, 19.1], [73.0, 19.0]]
                )
            },
            422,
            "hub_ops.area_invalid",
        ),
        ({"timings": {"packing": "03:00"}}, 422, "hub_ops.timings_invalid"),
        ({"timings": {"surplus": "25:00"}}, 422, "hub_ops.timings_invalid"),
        ({"timings": {"dispatch": "6:30"}}, 422, "hub_ops.timings_invalid"),
    ],
)
async def test_a_hub_is_refused_when_invalid(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    changes: dict[str, Any],
    status: int,
    code: str | None,
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    await db_session.commit()

    refused = await api.post("/v1/admin/hubs", json=hub_body(**changes), headers={**admin, **key()})

    assert refused.status_code == status, refused.text
    assert refused.json()["code"] == (code or "hub_ops.area_invalid")


async def test_patching_a_hub_needs_its_version_and_reports_what_changed(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    hub = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    url = f"/v1/admin/hubs/{hub['id']}"
    await db_session.commit()

    missing = await api.patch(url, json={"name": "Vashi"}, headers=admin)
    assert missing.status_code == 428
    stale = await api.patch(url, json={"name": "Vashi"}, headers={**admin, "If-Match": '"7"'})
    assert stale.status_code == 412
    assert stale.json()["code"] == "request.version_mismatch"
    slug = await api.patch(url, json={"slug": "vashi-2"}, headers={**admin, "If-Match": '"1"'})
    assert slug.status_code == 422  # the slug never changes
    null_name = await api.patch(url, json={"name": None}, headers={**admin, "If-Match": '"1"'})
    assert null_name.status_code == 422

    patch = {
        "name": "Vashi hub",  # unchanged
        "status": "paused",
        "service_area": area(SQUARE),
        "timings": {"surplus": "13:00"},
        "location": {"lat": 19.08, "lng": 73.0},
    }
    changed = await api.patch(url, json=patch, headers={**admin, "If-Match": '"1"'})
    assert changed.status_code == 200, changed.text
    assert changed.headers["ETag"] == '"2"'
    body = changed.json()
    assert (body["status"], body["version"], body["timings"]["surplus"]) == ("paused", 2, "13:00")
    assert body["service_area"] == area(SQUARE)
    assert await events(db_session, "hub_ops.hub_updated", hub["id"]) == [
        {"hub_id": hub["id"], "changed": ["status", "location", "service_area", "timings"]}
    ]

    same = await api.patch(url, json=patch, headers={**admin, "If-Match": '"2"'})
    assert same.json()["version"] == 2  # nothing changed: no new version, no event
    cleared = await api.patch(url, json={"service_area": None}, headers={**admin, "If-Match": "2"})
    assert (cleared.json()["service_area"], cleared.json()["version"]) == (None, 3)
    assert len(await events(db_session, "hub_ops.hub_updated", hub["id"])) == 2

    fee = await api.patch(
        url, json={"pre_live_box_fee_paise": 0}, headers={**admin, "If-Match": "3"}
    )
    assert (fee.json()["pre_live_box_fee_paise"], fee.json()["version"]) == (0, 4)
    assert (await events(db_session, "hub_ops.hub_updated", hub["id"]))[-1]["changed"] == [
        "pre_live_box_fee_paise"
    ]
    too_much = await api.patch(
        url, json={"pre_live_box_fee_paise": 10_001}, headers={**admin, "If-Match": "4"}
    )
    assert too_much.status_code == 422

    unknown = await api.patch(
        f"/v1/admin/hubs/{uuid.uuid4()}",
        json={"name": "Nowhere"},
        headers={**admin, "If-Match": "1"},
    )
    assert unknown.json()["code"] == "hub_ops.hub_not_found"


async def test_hub_staff_see_only_their_hubs_and_only_admins_write(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    manager = await as_user(api, google, VIKRAM)
    packer = await as_user(api, google, KAVITA)
    customer = await as_user(api, google, PRIYA)
    await db_session.commit()

    everything = (await api.get("/v1/hubs", headers=admin)).json()["items"]
    assert [hub["slug"] for hub in everything] == ["kharghar", "vashi"]
    for staff in (manager, packer):
        mine = (await api.get("/v1/hubs", headers=staff)).json()["items"]
        assert mine == [
            {"id": str(SEED_HUB_ID), "slug": "kharghar", "name": "Kharghar hub", "status": "active"}
        ]
        assert (await api.get(f"/v1/hubs/{SEED_HUB_ID}", headers=staff)).status_code == 200
        hidden = await api.get(f"/v1/hubs/{other['id']}", headers=staff)
        assert (hidden.status_code, hidden.json()["code"]) == (404, "hub_ops.hub_not_found")

    assert (await api.get("/v1/hubs", headers=customer)).status_code == 403
    refused = await api.post("/v1/admin/hubs", json=hub_body("thane"), headers={**manager, **key()})
    assert refused.status_code == 403
    assert (await api.get(f"/v1/hubs/{uuid.uuid4()}", headers=admin)).status_code == 404


async def test_the_facade_gives_hubs_and_the_active_ones(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    paused = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    await api.patch(
        f"/v1/admin/hubs/{paused['id']}",
        json={"status": "paused"},
        headers={**admin, "If-Match": "1"},
    )

    ref = await hub_ops_api.get_hub(db_session, SEED_HUB_ID)

    assert ref is not None
    assert (ref.slug, ref.status, ref.lat, ref.lng) == ("kharghar", "active", 19.0330, 73.0297)
    assert ref.timings["packing"] == "05:00"
    assert await hub_ops_api.get_hub(db_session, uuid.uuid4()) is None
    assert await hub_ops_api.active_hub_ids(db_session) == [SEED_HUB_ID]


async def test_seeding_the_hub_again_changes_nothing(db_session: AsyncSession) -> None:
    hub = await db_session.get(Hub, SEED_HUB_ID)
    assert hub is not None and hub.data_source == "synthetic" and hub.service_area is not None

    again = await seed_hubs(db_session)

    assert again == [("kharghar", False)]
    await db_session.refresh(hub)
    assert hub.version == 1
    vendors = list((await db_session.execute(select(Vendor))).scalars())
    assert [(v.name, v.is_default) for v in vendors] == [("Shinde Traders", True)]
    assert len(await events(db_session, "hub_ops.hub_created", str(SEED_HUB_ID))) == 1
    assert await events(db_session, "hub_ops.hub_updated", str(SEED_HUB_ID)) == []


async def test_every_hub_id_in_the_seed_world_is_a_real_hub(db_session: AsyncSession) -> None:
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)

    used: set[uuid.UUID] = set()
    for column in (
        Society.hub_id,
        Household.hub_id,
        PriceList.hub_id,
        Slot.hub_id,
        UserRole.scope_id,
    ):
        query = select(column).where(column.is_not(None)).distinct()
        if column is UserRole.scope_id:
            query = query.where(UserRole.scope_type == "hub")
        used |= {hub_id for hub_id in (await db_session.execute(query)).scalars() if hub_id}
    hubs = set((await db_session.execute(select(Hub.id))).scalars())

    assert SEED_HUB_ID in used
    assert used <= hubs
