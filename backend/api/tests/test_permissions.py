"""The permission check (platform/permissions.py): grants, scopes and X-Active-Role."""

import uuid
from typing import Annotated

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gharbasket.main import create_app
from gharbasket.platform.permissions import PERMISSIONS, Grant, Permit, require
from gharbasket.platform.tokens import issue_access_token

from .conftest import make_settings

HUB_A = uuid.UUID("0199a1b2-0000-7000-8000-00000000000a")
HUB_B = uuid.UUID("0199a1b2-0000-7000-8000-00000000000b")
SOCIETY = uuid.UUID("0199a1b2-0000-7000-8000-0000000000c1")

# Test-only permission names, so the real registry entries are untouched.
PERMISSIONS.allow("hub_manager", "test_orders:read")
PERMISSIONS.allow("admin", "test_orders:read")
PERMISSIONS.allow("captain", "test_handover:write")


@pytest.mark.parametrize(
    ("value", "grant"),
    [
        ("customer", Grant("customer", None)),
        ("admin", Grant("admin", None)),
        (f"hub_manager:{HUB_A}", Grant("hub_manager", HUB_A)),
        (f"captain:{SOCIETY}", Grant("captain", SOCIETY)),
        ("hub_manager", None),  # a hub role without its hub
        (f"customer:{HUB_A}", None),  # customers are not scoped
        ("captain:not-a-uuid", None),
        ("superuser", None),
        ("", None),
    ],
)
def test_parsing_grants(value: str, grant: Grant | None) -> None:
    assert Grant.parse(value) == grant


def test_only_known_roles_can_be_given_permissions() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        PERMISSIONS.allow("superuser", "everything")


def test_a_scoped_grant_covers_only_its_own_hub() -> None:
    permit = Permit(principal=None, permission="p", grants=(Grant("hub_manager", HUB_A),))  # pyright: ignore[reportArgumentType]
    admin = Permit(principal=None, permission="p", grants=(Grant("admin", None),))  # pyright: ignore[reportArgumentType]

    assert permit.allows(HUB_A) and not permit.allows(HUB_B)
    assert admin.allows(HUB_A) and admin.allows(HUB_B)


@pytest.fixture
def client() -> TestClient:
    app: FastAPI = create_app(make_settings())

    @app.get("/v1/_test/hubs/{hub_id}/orders")
    async def hub_orders(
        hub_id: uuid.UUID, permit: Annotated[Permit, require("test_orders:read")]
    ) -> dict[str, str]:
        permit.ensure(hub_id)
        return {"via": ",".join(str(g) for g in permit.grants)}

    @app.post("/v1/_test/handovers")
    async def handover(permit: Annotated[Permit, require("test_handover:write")]) -> None:
        return None

    return TestClient(app)


def _headers(*roles: str, active: str | None = None) -> dict[str, str]:
    token, _ = issue_access_token(
        make_settings(), user_id=uuid.uuid4(), session_id=uuid.uuid4(), roles=roles
    )
    headers = {"Authorization": f"Bearer {token}"}
    if active is not None:
        headers["X-Active-Role"] = active
    return headers


def test_a_hub_manager_sees_their_own_hub_only(client: TestClient) -> None:
    manager = _headers("customer", f"hub_manager:{HUB_A}")

    own = client.get(f"/v1/_test/hubs/{HUB_A}/orders", headers=manager)
    other = client.get(f"/v1/_test/hubs/{HUB_B}/orders", headers=manager)

    assert own.status_code == 200
    assert own.json() == {"via": f"hub_manager:{HUB_A}"}
    assert other.status_code == 403
    assert other.json()["code"] == "auth.forbidden"


def test_an_admin_sees_every_hub(client: TestClient) -> None:
    response = client.get(f"/v1/_test/hubs/{HUB_B}/orders", headers=_headers("admin"))

    assert response.status_code == 200


def test_a_customer_has_no_staff_permissions(client: TestClient) -> None:
    response = client.get(f"/v1/_test/hubs/{HUB_A}/orders", headers=_headers("customer"))

    assert response.status_code == 403
    assert response.json()["code"] == "auth.forbidden"


def test_the_active_role_narrows_what_counts(client: TestClient) -> None:
    captain = f"captain:{SOCIETY}"
    as_captain = client.post(
        "/v1/_test/handovers", headers=_headers("customer", captain, active=captain)
    )
    as_customer = client.post(
        "/v1/_test/handovers", headers=_headers("customer", captain, active="customer")
    )

    assert as_captain.status_code == 200
    assert as_customer.status_code == 403


# A fixed id: a random one in the test id would differ between xdist workers, which refuse to run.
OTHER_SOCIETY = uuid.UUID("0199a1b2-0000-7000-8000-0000000000c2")


@pytest.mark.parametrize("active", ["admin", f"captain:{OTHER_SOCIETY}", "garbage"])
def test_an_active_role_the_caller_does_not_hold_is_refused(
    client: TestClient, active: str
) -> None:
    response = client.post(
        "/v1/_test/handovers", headers=_headers("customer", f"captain:{SOCIETY}", active=active)
    )

    assert response.status_code == 403
    assert response.json()["code"] == "auth.role_not_granted"


def test_no_token_is_401_before_any_permission_check(client: TestClient) -> None:
    response = client.post("/v1/_test/handovers")

    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"


# --- a person with a customer role and a scoped staff role (the mixed-role hole) ----------------

PERMISSIONS.allow("customer", "test_mixed:read")
PERMISSIONS.allow("hub_manager", "test_mixed:read")
PERMISSIONS.allow("admin", "test_mixed:read")


def _permit(*grants: Grant) -> Permit:
    return Permit(principal=None, permission="p", grants=grants)  # pyright: ignore[reportArgumentType]


def test_a_customer_grant_covers_no_scope_even_beside_a_scoped_one() -> None:
    customer = Grant("customer", None)
    mixed = _permit(customer, Grant("hub_manager", HUB_A))

    assert mixed.allows(HUB_A) and not mixed.allows(HUB_B)
    assert not _permit(customer).allows(HUB_A)
    assert not mixed.is_global and mixed.has_role("customer")
    assert _permit(customer, Grant("admin", None)).is_global
    assert not _permit(Grant("captain", SOCIETY)).allows(HUB_A)


@pytest.fixture
def mixed_client() -> TestClient:
    app: FastAPI = create_app(make_settings())

    @app.get("/v1/_test/mixed/hubs/{hub_id}")
    async def hub_thing(
        hub_id: uuid.UUID, permit: Annotated[Permit, require("test_mixed:read")]
    ) -> dict[str, str]:
        permit.ensure(hub_id)
        return {"via": ",".join(str(g) for g in permit.grants)}

    return TestClient(app)


@pytest.mark.parametrize("active", [None, "customer", f"hub_manager:{HUB_A}"])
def test_a_customer_and_hub_manager_never_reaches_another_hub(
    mixed_client: TestClient, active: str | None
) -> None:
    headers = _headers("customer", f"hub_manager:{HUB_A}", active=active)

    other = mixed_client.get(f"/v1/_test/mixed/hubs/{HUB_B}", headers=headers)
    own = mixed_client.get(f"/v1/_test/mixed/hubs/{HUB_A}", headers=headers)

    assert (other.status_code, other.json()["code"]) == (403, "auth.forbidden")
    # Her own hub through the hub role; as a customer alone, no hub at all.
    assert own.status_code == (403 if active == "customer" else 200)


@pytest.mark.parametrize("roles", [("customer",), ("customer", "admin")])
def test_single_roles_and_admins_are_unchanged(
    mixed_client: TestClient, roles: tuple[str, ...]
) -> None:
    answer = mixed_client.get(f"/v1/_test/mixed/hubs/{HUB_B}", headers=_headers(*roles))

    assert answer.status_code == (200 if "admin" in roles else 403)
