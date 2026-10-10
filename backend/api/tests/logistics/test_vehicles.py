"""Vehicles (M7 §3.1): per hub, labels unique ignoring case, defaults by kind, a default rider
who rides for the hub, deactivated instead of deleted, and only the hub's own staff."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import ARJUN, KAVITA, NEHA, SANJAY, VIKRAM, as_user, key, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("people")]

VEHICLES = f"/v1/hubs/{SEED_HUB_ID}/vehicles"


def vehicle(label: str = "EV-1", **changes: Any) -> dict[str, Any]:
    return {"label": label, "kind": "cargo_3w", **changes}


def hub_body(slug: str = "vashi") -> dict[str, object]:
    return {
        "slug": slug,
        "name": "Vashi hub",
        "address": "Sector 17, Vashi, Navi Mumbai 400703",
        "location": {"lat": 19.0771, "lng": 72.9986},
    }


async def test_a_hub_manager_adds_vehicles_with_the_kinds_defaults(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    arjun = await user_id(db_session, ARJUN)
    headers = {**manager, **key()}

    body = vehicle(registration="mh 46 ab-1234", default_rider_user_id=str(arjun))
    created = await api.post(VEHICLES, json=body, headers=headers)
    replayed = await api.post(VEHICLES, json=body, headers=headers)
    bike = await api.post(
        VEHICLES, json=vehicle("bike 2", kind="two_wheeler"), headers={**manager, **key()}
    )
    small = await api.post(
        VEHICLES,
        json=vehicle("ev-0", capacity_kg=300, capacity_crates=18),
        headers={**manager, **key()},
    )

    assert created.status_code == 201, created.text
    assert created.headers["ETag"] == '"1"'
    van = created.json()
    assert replayed.json() == van
    assert (van["registration"], van["kind"], van["active"], van["version"]) == (
        "MH46AB1234",
        "cargo_3w",
        True,
        1,
    )
    assert (van["capacity_kg"], van["capacity_crates"], van["capacity_tiffins"]) == (400, 24, 0)
    assert van["default_rider"] == {"user_id": str(arjun), "name": "Arjun Pawar"}
    two = bike.json()
    assert (two["capacity_kg"], two["capacity_crates"], two["capacity_tiffins"]) == (40, 0, 30)
    assert two["default_rider"] is None
    assert (small.json()["capacity_kg"], small.json()["capacity_crates"]) == (300, 18)

    listed = await api.get(VEHICLES, headers=manager)
    assert listed.headers["Cache-Control"] == "no-store"
    assert [v["label"] for v in listed.json()["items"]] == ["bike 2", "ev-0", "EV-1"]  # by label
    vans = (await api.get(f"{VEHICLES}?kind=cargo_3w", headers=manager)).json()["items"]
    assert [v["label"] for v in vans] == ["ev-0", "EV-1"]


async def test_labels_capacities_and_riders_are_checked(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    elsewhere = await api.post(
        f"/v1/hubs/{other['id']}/vehicles", json=vehicle(), headers={**admin, **key()}
    )
    assert elsewhere.status_code == 201  # the same label at another hub
    first = await api.post(VEHICLES, json=vehicle(), headers={**admin, **key()})
    assert first.status_code == 201
    await db_session.commit()

    taken = await api.post(VEHICLES, json=vehicle("  ev-1 "), headers={**admin, **key()})
    assert (taken.status_code, taken.json()["code"]) == (409, "logistics.vehicle_label_taken")

    kavita = await user_id(db_session, KAVITA)  # a packer, not a rider
    arjun = await user_id(db_session, ARJUN)
    for bad, code in (
        (vehicle("V2", default_rider_user_id=str(kavita)), "logistics.rider_invalid"),
        (vehicle("V2", default_rider_user_id=str(uuid.uuid4())), "logistics.rider_invalid"),
        (vehicle("V2", capacity_crates=0), "logistics.capacity_invalid"),
        (vehicle("V2", kind="two_wheeler", capacity_tiffins=0), "logistics.capacity_invalid"),
        (vehicle("V2", capacity_kg=5), "request.invalid"),
        (vehicle("V2", capacity_crates=101), "request.invalid"),
        (vehicle("V2", kind="truck"), "request.invalid"),
        (vehicle("x" * 21), "request.invalid"),
        (vehicle("   "), "request.invalid"),
        (vehicle("V2", registration="MH-1"), "request.invalid"),
        (vehicle("V2", extra=1), "request.invalid"),
    ):
        refused = await api.post(VEHICLES, json=bad, headers={**admin, **key()})
        assert (refused.status_code, refused.json()["code"]) == (422, code), bad
    # Arjun rides for the seed hub, not for the other one.
    not_theirs = await api.post(
        f"/v1/hubs/{other['id']}/vehicles",
        json=vehicle("V2", default_rider_user_id=str(arjun)),
        headers={**admin, **key()},
    )
    assert not_theirs.json()["code"] == "logistics.rider_invalid"
    unknown = await api.post(
        f"/v1/hubs/{uuid.uuid4()}/vehicles", json=vehicle(), headers={**admin, **key()}
    )
    assert (unknown.status_code, unknown.json()["code"]) == (404, "hub_ops.hub_not_found")
    missing = await api.get(f"/v1/hubs/{uuid.uuid4()}/vehicles", headers=admin)
    assert missing.json()["code"] == "hub_ops.hub_not_found"


async def test_patching_and_deactivating_a_vehicle(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    arjun = await user_id(db_session, ARJUN)
    van = (await api.post(VEHICLES, json=vehicle(), headers={**manager, **key()})).json()
    await api.post(VEHICLES, json=vehicle("EV-2"), headers={**manager, **key()})
    url = f"/v1/vehicles/{van['id']}"
    await db_session.commit()

    unconditional = await api.patch(url, json={"active": False}, headers=manager)
    assert (unconditional.status_code, unconditional.json()["code"]) == (
        428,
        "request.precondition_required",
    )
    stale = await api.patch(url, json={"active": False}, headers={**manager, "If-Match": "9"})
    assert (stale.status_code, stale.json()["code"]) == (412, "request.version_mismatch")
    clash = await api.patch(url, json={"label": "ev-2"}, headers={**manager, "If-Match": "1"})
    assert (clash.status_code, clash.json()["code"]) == (409, "logistics.vehicle_label_taken")
    null_kind = await api.patch(url, json={"kind": None}, headers={**manager, "If-Match": "1"})
    assert null_kind.status_code == 422
    # Turning it into a two-wheeler needs a tiffin capacity too.
    bike = await api.patch(url, json={"kind": "two_wheeler"}, headers={**manager, "If-Match": "1"})
    assert bike.json()["code"] == "logistics.capacity_invalid"
    stranger = await api.patch(
        url, json={"default_rider_user_id": str(uuid.uuid4())}, headers={**manager, "If-Match": "1"}
    )
    assert stranger.json()["code"] == "logistics.rider_invalid"

    patch = {
        "active": False,
        "capacity_kg": 350,
        "registration": "MH46ZZ0001",
        "default_rider_user_id": str(arjun),
    }
    off = await api.patch(url, json=patch, headers={**manager, "If-Match": "1"})
    assert off.status_code == 200, off.text
    assert off.headers["ETag"] == '"2"'
    body = off.json()
    assert (body["active"], body["capacity_kg"], body["registration"], body["version"]) == (
        False,
        350,
        "MH46ZZ0001",
        2,
    )
    assert body["default_rider"]["name"] == "Arjun Pawar"
    active = (await api.get(f"{VEHICLES}?active=true", headers=manager)).json()["items"]
    assert [v["label"] for v in active] == ["EV-2"]
    inactive = (await api.get(f"{VEHICLES}?active=false", headers=manager)).json()["items"]
    assert [v["id"] for v in inactive] == [van["id"]]

    same = await api.patch(url, json={"active": False}, headers={**manager, "If-Match": "2"})
    assert same.json()["version"] == 2  # nothing changed
    cleared = await api.patch(
        url,
        json={"default_rider_user_id": None, "registration": None},
        headers={**manager, "If-Match": "2"},
    )
    assert (cleared.json()["default_rider"], cleared.json()["registration"]) == (None, None)
    unknown = await api.patch(
        f"/v1/vehicles/{uuid.uuid4()}", json={}, headers={**manager, "If-Match": "1"}
    )
    assert (unknown.status_code, unknown.json()["code"]) == (404, "logistics.vehicle_not_found")


async def test_vehicles_belong_to_their_hub_staff_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    theirs = (
        await api.post(
            f"/v1/hubs/{other['id']}/vehicles", json=vehicle(), headers={**admin, **key()}
        )
    ).json()
    manager = await as_user(api, google, VIKRAM)
    owner = await as_user(api, google, SANJAY)
    packer = await as_user(api, google, KAVITA)
    rider = await as_user(api, google, ARJUN)
    await db_session.commit()

    for method, path in (
        ("GET", f"/v1/hubs/{other['id']}/vehicles"),
        ("POST", f"/v1/hubs/{other['id']}/vehicles"),
    ):
        refused = await api.request(method, path, json=vehicle(), headers={**manager, **key()})
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    hidden = await api.patch(
        f"/v1/vehicles/{theirs['id']}", json={"active": False}, headers={**manager, "If-Match": "1"}
    )
    assert (hidden.status_code, hidden.json()["code"]) == (404, "logistics.vehicle_not_found")

    # The franchise owner reads the fleet but does not change it; packers and riders see neither.
    assert (await api.get(VEHICLES, headers=owner)).status_code == 200
    assert (await api.post(VEHICLES, json=vehicle(), headers={**owner, **key()})).status_code == 403
    for staff in (packer, rider):
        assert (await api.get(VEHICLES, headers=staff)).status_code == 403
