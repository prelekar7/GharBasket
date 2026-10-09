"""Vendors (M6 §3.1): per hub, unique names, one default, deactivated instead of deleted, and only
the hub's own manager (or an admin) may change them."""

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import NEHA, SANJAY, VIKRAM, as_user, hub_body, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("people")]

VENDORS = f"/v1/hubs/{SEED_HUB_ID}/vendors"


def vendor(name: str = "Kisan FPO", **changes: Any) -> dict[str, Any]:
    return {"name": name, "kind": "fpo", **changes}


async def test_a_hub_manager_adds_vendors_and_one_is_the_default(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    headers = {**manager, **key()}

    body = vendor(phone="098200 12345", is_default=True, notes="  Tue and Fri  ")
    created = await api.post(VENDORS, json=body, headers=headers)
    replayed = await api.post(VENDORS, json=body, headers=headers)

    assert created.status_code == 201, created.text
    added = created.json()
    assert replayed.json() == added
    assert added["phone"] == "+919820012345"
    assert added["notes"] == "Tue and Fri"
    assert (added["is_default"], added["whatsapp"], added["active"]) == (True, True, True)
    assert (added["score"], added["version"]) == (None, 1)
    listed = (await api.get(VENDORS, headers=manager)).json()["items"]
    # By name; the seed's default vendor stopped being the default (and got a new version).
    assert [(v["name"], v["is_default"], v["version"]) for v in listed] == [
        ("Kisan FPO", True, 1),
        ("Shinde Traders", False, 2),
    ]


async def test_vendor_names_are_unique_per_hub_ignoring_case(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    elsewhere = await api.post(
        f"/v1/hubs/{other['id']}/vendors", json=vendor("Shinde Traders"), headers={**admin, **key()}
    )
    assert elsewhere.status_code == 201
    await db_session.commit()

    taken = await api.post(VENDORS, json=vendor("shinde  TRADERS"), headers={**admin, **key()})

    assert (taken.status_code, taken.json()["code"]) == (409, "hub_ops.vendor_name_taken")
    for bad in (vendor(phone="12345"), vendor(kind="broker"), vendor("x"), vendor(extra=1)):
        refused = await api.post(VENDORS, json=bad, headers={**admin, **key()})
        assert refused.status_code == 422, bad
    unknown = await api.post(
        f"/v1/hubs/{uuid.uuid4()}/vendors", json=vendor(), headers={**admin, **key()}
    )
    assert unknown.json()["code"] == "hub_ops.hub_not_found"


async def test_patching_and_deactivating_a_vendor(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    seed_vendor = (await api.get(VENDORS, headers=manager)).json()["items"][0]
    kisan = (await api.post(VENDORS, json=vendor(), headers={**manager, **key()})).json()
    url = f"/v1/vendors/{seed_vendor['id']}"
    await db_session.commit()

    stale = await api.patch(url, json={"whatsapp": False}, headers={**manager, "If-Match": "9"})
    assert (stale.status_code, stale.json()["code"]) == (412, "request.version_mismatch")
    clash = await api.patch(url, json={"name": "KISAN fpo"}, headers={**manager, "If-Match": "1"})
    assert (clash.status_code, clash.json()["code"]) == (409, "hub_ops.vendor_name_taken")
    null_kind = await api.patch(url, json={"kind": None}, headers={**manager, "If-Match": "1"})
    assert null_kind.status_code == 422

    patch = {"phone": None, "notes": None, "active": False, "kind": "wholesaler"}
    off = await api.patch(url, json=patch, headers={**manager, "If-Match": "1"})
    assert off.status_code == 200, off.text
    assert off.headers["ETag"] == '"2"'
    body = off.json()
    # Deactivating clears the default; the vendor stays, with its history.
    assert (body["active"], body["is_default"], body["phone"], body["kind"]) == (
        False,
        False,
        None,
        "wholesaler",
    )
    assert [
        v["name"]
        for v in (await api.get(f"{VENDORS}?active=true", headers=manager)).json()["items"]
    ] == ["Kisan FPO"]
    inactive = (await api.get(f"{VENDORS}?active=false", headers=manager)).json()["items"]
    assert [v["id"] for v in inactive] == [seed_vendor["id"]]

    same = await api.patch(url, json={"active": False}, headers={**manager, "If-Match": "2"})
    assert same.json()["version"] == 2  # nothing changed
    default = await api.patch(
        f"/v1/vendors/{kisan['id']}",
        json={"is_default": True},
        headers={**manager, "If-Match": "1"},
    )
    assert (default.json()["is_default"], default.json()["version"]) == (True, 2)
    unknown = await api.patch(
        f"/v1/vendors/{uuid.uuid4()}", json={}, headers={**manager, "If-Match": "1"}
    )
    assert unknown.json()["code"] == "hub_ops.vendor_not_found"


async def test_vendors_belong_to_their_hub_staff_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    theirs = (
        await api.post(f"/v1/hubs/{other['id']}/vendors", json=vendor(), headers={**admin, **key()})
    ).json()
    manager = await as_user(api, google, VIKRAM)
    owner = await as_user(api, google, SANJAY)
    await db_session.commit()

    for method, path in (
        ("GET", f"/v1/hubs/{other['id']}/vendors"),
        ("POST", f"/v1/hubs/{other['id']}/vendors"),
    ):
        refused = await api.request(method, path, json=vendor(), headers={**manager, **key()})
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    hidden = await api.patch(
        f"/v1/vendors/{theirs['id']}", json={"active": False}, headers={**manager, "If-Match": "1"}
    )
    assert (hidden.status_code, hidden.json()["code"]) == (404, "hub_ops.vendor_not_found")

    # The franchise owner reads the vendor list but does not change it.
    assert (await api.get(VENDORS, headers=owner)).status_code == 200
    write = await api.post(VENDORS, json=vendor(), headers={**owner, **key()})
    assert write.status_code == 403
