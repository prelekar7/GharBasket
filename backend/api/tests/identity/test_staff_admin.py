"""Staff lookup, revoke, pending grants and the hub's staff (M1 §3.6)."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import PendingRoleGrant, User, UserRole
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry
from gharbasket.platform.tokens import ACCESS_TOKEN_TTL, verify_access_token
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, seed_hubs, seed_societies

from ..conftest import make_settings
from .conftest import ClientFactory, bearer, sign_in
from .fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services]

SETTINGS = make_settings()
OTHER_HUB = uuid.UUID("0199a1b2-0000-7000-8000-0000000000bb")
GRANT_KEYS = {"id", "role", "scope_type", "scope_id", "scope_label", "status", "verified_at"}
USER_KEYS = {"id", "name", "phone_e164", "locale", "status", "last_seen_at", "roles"}
PENDING_KEYS = {
    "id",
    "phone_masked",
    "role",
    "scope_type",
    "scope_id",
    "scope_label",
    "status",
    "granted_at",
    "expires_at",
}
HUB_STAFF_KEYS = {
    "grant_id",
    "user_id",
    "name",
    "phone_masked",
    "role",
    "works_as",
    "society",
    "status",
    "since",
}


@pytest.fixture(autouse=True)
async def places(db_session: AsyncSession) -> None:
    await seed_hubs(db_session)
    await seed_societies(db_session)


def phone(n: int) -> str:
    return f"+91980001{n:04d}"


class Person:
    """A signed-in account; `headers` carry its roles (refreshed after they were added)."""

    def __init__(self, user_id: uuid.UUID, phone_e164: str, tokens: dict[str, Any]) -> None:
        self.id = user_id
        self.phone = phone_e164
        self.tokens = tokens
        self.headers = bearer(tokens["access_token"])


async def person(
    api: httpx2.AsyncClient,
    google: FakeGoogle,
    db: AsyncSession,
    n: int,
    *,
    name: str | None = None,
    roles: tuple[tuple[str, str | None, uuid.UUID | None], ...] = (),
) -> Person:
    """Sign in number `n`; give them `roles` as (role, scope_type, scope_id); refresh the token."""
    signed_in = await sign_in(api, google, phone_number=phone(n), sub=f"uid-{n}")
    user = (await db.execute(select(User).where(User.phone_e164 == phone(n)))).scalar_one()
    user.name = name
    for role, scope_type, scope_id in roles:
        db.add(
            UserRole(
                user_id=user.id,
                role=role,
                scope_type=scope_type,
                scope_id=scope_id,
                verified_at=datetime.now(UTC),
            )
        )
    await db.flush()
    if not roles:
        return Person(user.id, phone(n), signed_in)
    refreshed = await api.post(
        "/v1/auth/refresh", json={"refresh_token": signed_in["refresh_token"]}
    )
    assert refreshed.status_code == 200, refreshed.text
    return Person(user.id, phone(n), {**signed_in, **refreshed.json()})


async def admin(
    api: httpx2.AsyncClient, google: FakeGoogle, db: AsyncSession, n: int = 1
) -> Person:
    return await person(api, google, db, n, name="Asha Admin", roles=(("admin", None, None),))


def hub_role(role: str, hub_id: uuid.UUID = SEED_HUB_ID) -> tuple[str, str, uuid.UUID]:
    return (role, "hub", hub_id)


async def audit_of(db: AsyncSession, action: str) -> list[AuditEntry]:
    rows = await db.execute(select(AuditEntry).where(AuditEntry.action == action))
    return list(rows.scalars())


def users(response: httpx2.Response) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.text
    return response.json()["items"]


# --- who may call -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_line",
    [
        ("GET", "/v1/admin/users?staff=true"),
        ("GET", "/v1/admin/users?pending=true"),
        ("DELETE", f"/v1/admin/user-roles/{uuid.uuid4()}"),
    ],
    ids=["lookup", "pending-list", "revoke"],
)
async def test_the_admin_endpoints_are_for_admins_only(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    request_line: tuple[str, str],
) -> None:
    api = clients()
    method, path = request_line
    customer = await person(api, google, db_session, 2)
    manager = await person(api, google, db_session, 3, roles=(hub_role("hub_manager"),))

    anonymous = await api.request(method, path)
    as_customer = await api.request(method, path, headers=customer.headers)
    as_manager = await api.request(method, path, headers=manager.headers)

    assert anonymous.status_code == 401
    assert as_customer.status_code == as_manager.status_code == 403
    assert as_manager.json()["code"] == "auth.forbidden"


async def test_the_hub_staff_list_is_for_that_hubs_staff_and_admins(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    path = f"/v1/hubs/{SEED_HUB_ID}/staff"
    customer = await person(api, google, db_session, 2)
    rider = await person(api, google, db_session, 3, roles=(hub_role("rider"),))
    manager = await person(api, google, db_session, 4, roles=(hub_role("hub_manager"),))
    elsewhere = await person(
        api, google, db_session, 5, roles=(hub_role("hub_manager", OTHER_HUB),)
    )
    owner = await person(api, google, db_session, 6, roles=(hub_role("franchise_owner"),))
    boss = await admin(api, google, db_session)

    statuses = {
        "anonymous": (await api.get(path)).status_code,
        "customer": (await api.get(path, headers=customer.headers)).status_code,
        "rider": (await api.get(path, headers=rider.headers)).status_code,
        "other hub": (await api.get(path, headers=elsewhere.headers)).status_code,
        "manager": (await api.get(path, headers=manager.headers)).status_code,
        "owner": (await api.get(path, headers=owner.headers)).status_code,
        "admin": (await api.get(path, headers=boss.headers)).status_code,
    }
    refused = await api.get(path, headers=elsewhere.headers)

    assert statuses == {
        "anonymous": 401,
        "customer": 403,
        "rider": 403,
        "other hub": 403,
        "manager": 200,
        "owner": 200,
        "admin": 200,
    }
    assert refused.json()["code"] == "auth.forbidden"


# --- the lookup -------------------------------------------------------------------------------


async def test_lookup_by_phone_accepts_any_usual_way_of_typing_the_number(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    target = await person(
        api,
        google,
        db_session,
        2,
        name="Kavita More",
        roles=(hub_role("hub_manager"), ("captain", "society", SEED_SOCIETY_ID)),
    )
    ten_digits = target.phone[3:]

    found = {
        typed: await api.get("/v1/admin/users", params={"phone": typed}, headers=boss.headers)
        for typed in (
            target.phone,
            ten_digits,
            f"0{ten_digits}",
            f"91 {ten_digits[:5]} {ten_digits[5:]}",
        )
    }
    missing = await api.get("/v1/admin/users", params={"phone": "9812345678"}, headers=boss.headers)

    for response in found.values():
        (item,) = users(response)
        assert item["id"] == str(target.id)
        assert response.headers["cache-control"] == "no-store"
    assert users(missing) == []
    assert missing.json()["next_cursor"] is None

    (item,) = users(found[target.phone])
    assert set(item) == USER_KEYS
    assert item["name"] == "Kavita More"
    assert item["status"] == "active"
    assert item["last_seen_at"] is not None
    assert all(set(grant) == GRANT_KEYS for grant in item["roles"])
    by_role = {grant["role"]: grant for grant in item["roles"]}
    assert set(by_role) == {"customer", "hub_manager", "captain"}  # the customer role included
    assert by_role["hub_manager"]["scope_label"] == "Kharghar hub"
    assert by_role["hub_manager"]["scope_type"] == "hub"
    assert by_role["captain"]["scope_label"] == "Sunrise Towers"
    assert by_role["customer"]["scope_label"] is None


@pytest.mark.parametrize(
    "query",
    [
        {},
        {"phone": "9812345678", "q": "Kavita"},
        {"staff": "true", "pending": "true"},
        {"staff": "false"},
        {"pending": "false"},
        {"phone": "12345"},
        {"phone": "not a number"},
        {"q": "K"},
        {"q": "K" * 61},
        {"q": "  x "},
        {"staff": "true", "limit": "51"},
        {"staff": "true", "limit": "0"},
    ],
    ids=[
        "no-filter",
        "two-filters",
        "staff-and-pending",
        "staff-false",
        "pending-false",
        "short-number",
        "text-number",
        "one-letter",
        "too-long",
        "blank-after-trim",
        "limit-too-big",
        "limit-zero",
    ],
)
async def test_a_bad_lookup_is_422_and_is_not_logged(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, query: dict[str, str]
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)

    response = await api.get("/v1/admin/users", params=query, headers=boss.headers)

    assert response.status_code == 422
    assert response.json()["code"] == "request.invalid"
    assert await audit_of(db_session, "identity.users_looked_up") == []


async def test_every_lookup_is_audit_logged_without_the_number_or_the_name(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    target = await person(api, google, db_session, 2, name="Kavita More")
    await person(api, google, db_session, 3, name="Kavita Rao", roles=(hub_role("packer"),))
    hub_manager = await person(api, google, db_session, 4, roles=(hub_role("hub_manager"),))

    await api.get("/v1/admin/users", params={"phone": target.phone}, headers=boss.headers)
    await api.get("/v1/admin/users", params={"phone": "9812345678"}, headers=boss.headers)
    by_name = await api.get("/v1/admin/users", params={"q": "Kavita"}, headers=boss.headers)
    await api.get("/v1/admin/users", params={"staff": "true"}, headers=boss.headers)
    await api.get("/v1/admin/users", params={"pending": "true"}, headers=boss.headers)
    # A refused caller leaves no entry: nothing was looked up.
    await api.get("/v1/admin/users", params={"phone": target.phone}, headers=hub_manager.headers)

    entries = await audit_of(db_session, "identity.users_looked_up")
    assert [e.after["filter"] for e in entries if e.after] == [
        "phone",
        "phone",
        "q",
        "staff",
        "pending",
    ]
    assert {e.actor_user_id for e in entries} == {boss.id}
    assert entries[0].after is not None
    assert entries[0].after["user_ids"] == [str(target.id)]
    assert entries[1].after is not None
    assert entries[1].after["user_ids"] == []
    assert entries[2].after is not None
    assert set(entries[2].after["user_ids"]) == {u["id"] for u in users(by_name)}
    everything = " ".join(str((e.before, e.after, e.entity_id, e.entity_type)) for e in entries)
    assert target.phone[3:] not in everything
    assert "Kavita" not in everything


async def test_lookup_by_name_ranks_the_best_match_first_and_pages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    await person(api, google, db_session, 2, name="Priya Deshmukh")
    await person(api, google, db_session, 3, name="Priyanka Chopra Joshi")
    await person(api, google, db_session, 4, name="Rahul Priya")
    await person(api, google, db_session, 5, name="Suresh Patil")
    await person(api, google, db_session, 6)  # no name at all

    first = await api.get(
        "/v1/admin/users", params={"q": "priya", "limit": 2}, headers=boss.headers
    )
    second = await api.get(
        "/v1/admin/users",
        params={"q": "priya", "limit": 2, "cursor": first.json()["next_cursor"]},
        headers=boss.headers,
    )
    nothing = await api.get("/v1/admin/users", params={"q": "zzzz"}, headers=boss.headers)
    other_filter = await api.get(
        "/v1/admin/users",
        params={"staff": "true", "cursor": first.json()["next_cursor"]},
        headers=boss.headers,
    )

    names = [u["name"] for u in users(first)] + [u["name"] for u in users(second)]
    assert len(names) == 3 and set(names) == {
        "Priya Deshmukh",
        "Rahul Priya",
        "Priyanka Chopra Joshi",
    }
    assert names[0] in {"Priya Deshmukh", "Rahul Priya"}  # a whole word beats a prefix
    assert first.json()["next_cursor"] is not None
    assert second.json()["next_cursor"] is None
    assert users(nothing) == []
    assert other_filter.status_code == 422
    assert other_filter.json()["code"] == "request.invalid_cursor"


async def test_a_name_with_like_wildcards_matches_only_itself(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    await person(api, google, db_session, 2, name="Anil Kumar")

    response = await api.get("/v1/admin/users", params={"q": "%%"}, headers=boss.headers)

    assert users(response) == []


async def test_staff_lists_everyone_with_a_staff_role_by_name(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)  # "Asha Admin"
    await person(api, google, db_session, 2, name="Zoya", roles=(hub_role("hub_manager"),))
    await person(api, google, db_session, 3, name="Bina", roles=(("catalog_editor", None, None),))
    await person(api, google, db_session, 4, name="Chitra", roles=(hub_role("rider"),))  # partner
    await person(api, google, db_session, 5, name="Dev")  # a customer only

    page = await api.get(
        "/v1/admin/users", params={"staff": "true", "limit": 2}, headers=boss.headers
    )
    rest = await api.get(
        "/v1/admin/users",
        params={"staff": "true", "limit": 2, "cursor": page.json()["next_cursor"]},
        headers=boss.headers,
    )

    assert [u["name"] for u in users(page)] == ["Asha Admin", "Bina"]
    assert [u["name"] for u in users(rest)] == ["Zoya"]
    assert rest.json()["next_cursor"] is None
    assert all(set(u) == USER_KEYS for u in users(page))


async def test_a_blocked_account_and_one_being_deleted_show_their_status(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    target = await person(api, google, db_session, 2)
    user = await db_session.get(User, target.id)
    assert user is not None
    user.status = "blocked"
    await db_session.flush()

    response = await api.get(
        "/v1/admin/users", params={"phone": target.phone}, headers=boss.headers
    )

    assert users(response)[0]["status"] == "blocked"


# --- pending grants ---------------------------------------------------------------------------


def grant_body(n: int, role: str = "hub_manager", **extra: Any) -> dict[str, Any]:
    scope = {"scope_id": str(SEED_HUB_ID)} if role in {"hub_manager", "franchise_owner"} else {}
    return {"phone_e164": phone(n), "role": role, **scope, **extra}


async def give(
    api: httpx2.AsyncClient, boss: Person, body: dict[str, Any], key: str = "k1"
) -> httpx2.Response:
    return await api.post(
        "/v1/admin/user-roles", json=body, headers={**boss.headers, "Idempotency-Key": key}
    )


async def test_a_staff_role_for_a_number_with_no_account_waits_and_the_first_sign_in_claims_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)

    waiting = await give(api, boss, grant_body(9))
    replay = await give(api, boss, grant_body(9))
    listed = await api.get("/v1/admin/users", params={"pending": "true"}, headers=boss.headers)

    assert waiting.status_code == 202, waiting.text
    body = waiting.json()
    assert set(body) == PENDING_KEYS
    assert body["status"] == "pending"
    assert body["phone_masked"] == "******" + phone(9)[-4:]
    assert body["scope_label"] == "Kharghar hub"
    assert datetime.fromisoformat(body["expires_at"]) - datetime.fromisoformat(
        body["granted_at"]
    ) == timedelta(days=30)
    assert replay.status_code == 202 and replay.headers["idempotent-replayed"] == "true"
    assert replay.json() == body
    assert users(listed) == [body]
    assert phone(9) not in waiting.text + listed.text
    (entry,) = await audit_of(db_session, "roles.granted")
    assert entry.actor_user_id == boss.id and entry.entity_type == "pending_role_grant"
    assert entry.entity_id == body["id"]
    assert entry.after is not None and entry.after["pending"] is True
    assert phone(9)[3:] not in str((entry.before, entry.after))

    # The first sign-in creates the account and the role together.
    newcomer = await sign_in(api, google, phone_number=phone(9), sub="uid-9")
    roles = (await api.get("/v1/me/roles", headers=bearer(newcomer["access_token"]))).json()
    claims = verify_access_token(make_settings(), newcomer["access_token"])

    assert newcomer["is_new_user"] is True
    assert {
        "role": "hub_manager",
        "scope_type": "hub",
        "scope_id": str(SEED_HUB_ID),
        "status": "verified",
    } in roles
    assert f"hub_manager:{SEED_HUB_ID}" in claims.roles  # usable at once, no second refresh
    assert (await db_session.execute(select(PendingRoleGrant))).first() is None
    claimed = [e for e in await audit_of(db_session, "roles.granted") if e.entity_type == "user"]
    assert [e.actor_user_id for e in claimed] == [boss.id]  # the admin who gave it
    assert claimed[0].entity_id == str(claims.user_id)
    listed_after = await api.get(
        "/v1/admin/users", params={"pending": "true"}, headers=boss.headers
    )
    assert users(listed_after) == []


async def test_a_second_sign_in_does_not_claim_again_and_a_number_with_an_account_gets_201(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    await give(api, boss, grant_body(9, "catalog_editor"))
    first = await sign_in(api, google, phone_number=phone(9), sub="uid-9")
    again = await sign_in(api, google, phone_number=phone(9), sub="uid-9")
    known = await person(api, google, db_session, 10)

    direct = await give(api, boss, grant_body(10), key="k2")

    roles = (await api.get("/v1/me/roles", headers=bearer(again["access_token"]))).json()
    assert first["is_new_user"] is True and again["is_new_user"] is False
    assert sorted(r["role"] for r in roles) == ["catalog_editor", "customer"]
    assert direct.status_code == 201
    assert direct.json()["user_id"] == str(known.id)


async def test_pending_grants_are_unique_per_number_role_and_scope(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    await give(api, boss, grant_body(9), key="a")

    same = await give(api, boss, grant_body(9), key="b")
    other_hub = await give(api, boss, {**grant_body(9), "scope_id": str(OTHER_HUB)}, key="c")
    other_role = await give(api, boss, grant_body(9, "franchise_owner"), key="d")

    assert same.status_code == 409 and same.json()["code"] == "identity.role_exists"
    assert other_hub.status_code == other_role.status_code == 202


async def test_a_partner_role_still_needs_the_account(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)

    responses = [
        await give(api, boss, {"phone_e164": phone(9), "role": role, **scope}, key=role)
        for role, scope in (
            ("rider", {"scope_id": str(SEED_HUB_ID)}),
            ("captain", {"scope_id": str(SEED_SOCIETY_ID)}),
        )
    ]

    assert [r.status_code for r in responses] == [404, 404]
    assert {r.json()["code"] for r in responses} == {"identity.user_not_found"}
    assert (await db_session.execute(select(PendingRoleGrant))).first() is None


async def test_a_pending_grant_validates_its_scope_like_any_grant(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)

    no_scope = await give(api, boss, {"phone_e164": phone(9), "role": "hub_manager"}, key="a")
    scoped_admin = await give(
        api, boss, {"phone_e164": phone(9), "role": "admin", "scope_id": str(SEED_HUB_ID)}, key="b"
    )

    assert no_scope.status_code == scoped_admin.status_code == 422
    assert {no_scope.json()["code"], scoped_admin.json()["code"]} == {"identity.invalid_scope"}


async def test_an_expired_pending_grant_is_not_claimed_not_listed_and_the_job_deletes_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    await give(api, boss, grant_body(8), key="a")
    await give(api, boss, grant_body(9), key="b")
    expired = (
        await db_session.execute(
            select(PendingRoleGrant).where(PendingRoleGrant.phone_e164 == phone(8))
        )
    ).scalar_one()
    expired.expires_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.flush()

    listed = await api.get("/v1/admin/users", params={"pending": "true"}, headers=boss.headers)
    late = await sign_in(api, google, phone_number=phone(8), sub="uid-8")
    run = await run_job(db_session, "expire_pending_grants", settings=SETTINGS)

    assert [g["phone_masked"][-4:] for g in users(listed)] == [phone(9)[-4:]]
    roles = (await api.get("/v1/me/roles", headers=bearer(late["access_token"]))).json()
    assert [r["role"] for r in roles] == ["customer"]
    assert run.status == "ok", run.error
    assert run.summary == {"expired": 1}  # the sign-in left it; only the job deletes it
    left = (await db_session.execute(select(PendingRoleGrant.phone_e164))).scalars().all()
    assert left == [phone(9)]


async def test_the_expiry_job_deletes_what_is_past_its_30_days(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    for n, expires in ((1, now - timedelta(days=1)), (2, now + timedelta(days=1))):
        db_session.add(PendingRoleGrant(phone_e164=phone(n), role="admin", expires_at=expires))
    await db_session.flush()

    run = await run_job(db_session, "expire_pending_grants", settings=SETTINGS, scheduled_for=now)

    assert run.status == "ok", run.error
    assert run.summary == {"expired": 1}
    left = (await db_session.execute(select(PendingRoleGrant.phone_e164))).scalars().all()
    assert left == [phone(2)]


async def test_the_pending_list_is_newest_first_and_pages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    ids = []
    for n in (4, 5, 6):
        ids.append(
            (await give(api, boss, grant_body(n, "catalog_editor"), key=str(n))).json()["id"]
        )
        # uuidv7 and created_at come from now(): the savepoint's transaction time is one value for
        # the whole test, so spread them apart as real days would.
    rows = (await db_session.execute(select(PendingRoleGrant))).scalars().all()
    for offset, row in enumerate(sorted(rows, key=lambda r: r.id)):
        row.created_at = datetime.now(UTC) - timedelta(hours=10 - offset)
    await db_session.flush()

    first = await api.get(
        "/v1/admin/users", params={"pending": "true", "limit": 2}, headers=boss.headers
    )
    rest = await api.get(
        "/v1/admin/users",
        params={"pending": "true", "limit": 2, "cursor": first.json()["next_cursor"]},
        headers=boss.headers,
    )

    assert [g["id"] for g in users(first)] == ids[::-1][:2]
    assert [g["id"] for g in users(rest)] == ids[::-1][2:]
    assert rest.json()["next_cursor"] is None


# --- revoke -----------------------------------------------------------------------------------


async def grant_of(db: AsyncSession, user_id: uuid.UUID, role: str) -> UserRole:
    return (
        await db.execute(select(UserRole).where(UserRole.user_id == user_id, UserRole.role == role))
    ).scalar_one()


async def test_a_revoked_role_stops_working_at_the_next_refresh_within_15_minutes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    manager = await person(
        api, google, db_session, 2, name="Meena", roles=(hub_role("hub_manager"),)
    )
    hub_staff = f"/v1/hubs/{SEED_HUB_ID}/staff"
    grant = await grant_of(db_session, manager.id, "hub_manager")
    assert (await api.get(hub_staff, headers=manager.headers)).status_code == 200

    revoked = await api.delete(f"/v1/admin/user-roles/{grant.id}", headers=boss.headers)

    assert revoked.status_code == 204 and revoked.content == b""
    assert (
        await db_session.execute(select(UserRole).where(UserRole.id == grant.id))
    ).first() is None
    # The access token already issued still carries the role, but only until it expires: the
    # contract's bound is one token lifetime (15 minutes), and it is never extended.
    claims = verify_access_token(make_settings(), manager.tokens["access_token"])
    assert f"hub_manager:{SEED_HUB_ID}" in claims.roles
    assert claims.expires_at - datetime.now(UTC) <= ACCESS_TOKEN_TTL
    assert timedelta(minutes=15) >= ACCESS_TOKEN_TTL
    # The next refresh reads user_roles: the new token has no role, and the endpoints refuse it.
    refreshed = await api.post(
        "/v1/auth/refresh", json={"refresh_token": manager.tokens["refresh_token"]}
    )
    assert refreshed.status_code == 200
    fresh = verify_access_token(make_settings(), refreshed.json()["access_token"])
    assert fresh.roles == ("customer",)
    after = await api.get(hub_staff, headers=bearer(refreshed.json()["access_token"]))
    assert after.status_code == 403 and after.json()["code"] == "auth.forbidden"
    # The person is still signed in as a customer: revoking a role is not a sign-out.
    assert (
        await api.get("/v1/me", headers=bearer(refreshed.json()["access_token"]))
    ).status_code == 200

    (entry,) = await audit_of(db_session, "roles.revoked")
    assert entry.actor_user_id == boss.id
    assert entry.entity_type == "user" and entry.entity_id == str(manager.id)
    assert entry.before is not None
    assert entry.before["role"] == "hub_manager" and entry.before["scope_id"] == str(SEED_HUB_ID)


async def test_a_token_older_than_its_lifetime_is_refused_even_with_the_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """The other half of the bound: a token cannot outlive 15 minutes (plus 30 s of leeway)."""
    from gharbasket.platform.tokens import issue_access_token

    api = clients()
    old, _ = issue_access_token(
        make_settings(),
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        roles=(f"hub_manager:{SEED_HUB_ID}",),
        now=datetime.now(UTC) - timedelta(minutes=16),
    )

    response = await api.get(f"/v1/hubs/{SEED_HUB_ID}/staff", headers=bearer(old))

    assert response.status_code == 401
    assert response.json()["code"] == "auth.invalid_token"


async def test_a_revoked_admin_cannot_refresh_back_into_the_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    other = await admin(api, google, db_session, 2)
    grant = await grant_of(db_session, other.id, "admin")

    removed = await api.delete(f"/v1/admin/user-roles/{grant.id}", headers=boss.headers)
    refreshed = await api.post(
        "/v1/auth/refresh", json={"refresh_token": other.tokens["refresh_token"]}
    )
    lookup = await api.get(
        "/v1/admin/users",
        params={"staff": "true"},
        headers=bearer(refreshed.json()["access_token"]),
    )

    assert removed.status_code == 204
    assert lookup.status_code == 403


async def test_a_pending_grant_is_cancelled_with_the_same_endpoint(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    waiting = (await give(api, boss, grant_body(9))).json()

    cancelled = await api.delete(f"/v1/admin/user-roles/{waiting['id']}", headers=boss.headers)
    again = await api.delete(f"/v1/admin/user-roles/{waiting['id']}", headers=boss.headers)
    newcomer = await sign_in(api, google, phone_number=phone(9), sub="uid-9")

    assert cancelled.status_code == 204
    assert again.status_code == 404 and again.json()["code"] == "identity.role_not_found"
    assert verify_access_token(make_settings(), newcomer["access_token"]).roles == ("customer",)
    (entry,) = await audit_of(db_session, "roles.revoked")
    assert entry.actor_user_id == boss.id and entry.entity_type == "pending_role_grant"
    assert entry.entity_id == waiting["id"]
    assert phone(9)[3:] not in str((entry.before, entry.after))


@pytest.mark.parametrize("role", ["customer", "rider", "packer", "kitchen_partner", "captain"])
async def test_customer_and_partner_roles_cannot_be_removed_here(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, role: str
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    scope = {"captain": ("society", SEED_SOCIETY_ID)}.get(role, ("hub", SEED_HUB_ID))
    target = await person(
        api, google, db_session, 2, roles=() if role == "customer" else ((role, *scope),)
    )
    grant_id = (await grant_of(db_session, target.id, role)).id

    response = await api.delete(f"/v1/admin/user-roles/{grant_id}", headers=boss.headers)

    assert response.status_code == 409
    assert response.json()["code"] == "identity.role_not_removable"
    assert await db_session.get(UserRole, grant_id, populate_existing=True) is not None
    assert await audit_of(db_session, "roles.revoked") == []


async def test_an_admin_cannot_remove_their_own_admin_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    await admin(api, google, db_session, 2)
    mine_id = (await grant_of(db_session, boss.id, "admin")).id

    response = await api.delete(f"/v1/admin/user-roles/{mine_id}", headers=boss.headers)

    assert response.status_code == 409
    assert response.json()["code"] == "identity.cannot_remove_own_admin"
    assert await db_session.get(UserRole, mine_id, populate_existing=True) is not None


async def test_the_last_admin_cannot_be_removed_even_by_an_admin_whose_role_just_went(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    first = await admin(api, google, db_session)
    second = await admin(api, google, db_session, 2)
    second_id = (await grant_of(db_session, second.id, "admin")).id
    first_id = (await grant_of(db_session, first.id, "admin")).id

    removed = await api.delete(f"/v1/admin/user-roles/{second_id}", headers=first.headers)
    # `second`'s token still says admin for up to 15 minutes; the database says first is the last.
    stale = await api.delete(f"/v1/admin/user-roles/{first_id}", headers=second.headers)

    assert removed.status_code == 204
    assert stale.status_code == 409 and stale.json()["code"] == "identity.last_admin"
    assert await db_session.get(UserRole, first_id, populate_existing=True) is not None


async def test_a_suspended_admin_can_be_removed_while_another_admin_remains(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    target = await person(api, google, db_session, 2, roles=(("admin", None, None),))
    (grant,) = (
        await db_session.execute(
            select(UserRole).where(UserRole.user_id == target.id, UserRole.role == "admin")
        )
    ).scalars()
    grant.status = "suspended"
    await db_session.flush()

    response = await api.delete(f"/v1/admin/user-roles/{grant.id}", headers=boss.headers)

    assert response.status_code == 204


async def test_revoke_validates_the_id(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)

    unknown = await api.delete(f"/v1/admin/user-roles/{uuid.uuid4()}", headers=boss.headers)
    malformed = await api.delete("/v1/admin/user-roles/not-a-uuid", headers=boss.headers)

    assert unknown.status_code == 404 and unknown.json()["code"] == "identity.role_not_found"
    assert malformed.status_code == 422


async def test_revoking_one_of_two_roles_leaves_the_other(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    target = await person(
        api,
        google,
        db_session,
        2,
        roles=(hub_role("hub_manager"), hub_role("franchise_owner")),
    )
    grant = await grant_of(db_session, target.id, "franchise_owner")

    await api.delete(f"/v1/admin/user-roles/{grant.id}", headers=boss.headers)

    roles = await identity_api.holds_role(db_session, target.id, "hub_manager", SEED_HUB_ID)
    assert roles is True
    assert (
        await identity_api.holds_role(db_session, target.id, "franchise_owner", SEED_HUB_ID)
        is False
    )


# --- the hub's staff --------------------------------------------------------------------------


async def test_the_hubs_staff_list_has_names_masked_phones_and_the_hubs_people_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    manager = await person(
        api, google, db_session, 2, name="Meena", roles=(hub_role("hub_manager"),)
    )
    await person(api, google, db_session, 3, name="Ravi", roles=(hub_role("rider"),))
    await person(api, google, db_session, 4, name="Anita", roles=(hub_role("rider"),))
    await person(api, google, db_session, 5, name="Pooja", roles=(hub_role("packer"),))
    await person(
        api, google, db_session, 6, name="Kavita", roles=(("captain", "society", SEED_SOCIETY_ID),)
    )
    await person(
        api, google, db_session, 7, name="Elsewhere", roles=(hub_role("rider", OTHER_HUB),)
    )
    await person(api, google, db_session, 8, name="Just a customer")
    await give(api, boss, grant_body(9, "franchise_owner"), key="invite")  # waiting for a sign-in
    await give(api, boss, {**grant_body(9), "scope_id": str(OTHER_HUB)}, key="other")  # another hub
    await give(api, boss, grant_body(9, "catalog_editor"), key="unscoped")  # not a hub's

    response = await api.get(f"/v1/hubs/{SEED_HUB_ID}/staff", headers=manager.headers)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    items = users(response)
    assert all(set(item) == HUB_STAFF_KEYS for item in items)
    assert [(i["role"], i["name"]) for i in items] == [
        ("captain", "Kavita"),
        ("franchise_owner", None),
        ("hub_manager", "Meena"),
        ("packer", "Pooja"),
        ("rider", "Anita"),
        ("rider", "Ravi"),
    ]
    by_name = {i["name"]: i for i in items}
    assert by_name["Kavita"]["works_as"] == "partner"
    assert by_name["Kavita"]["society"] == {"id": str(SEED_SOCIETY_ID), "name": "Sunrise Towers"}
    assert by_name["Meena"]["works_as"] == "staff" and by_name["Meena"]["society"] is None
    assert by_name["Meena"]["status"] == "verified" and by_name["Meena"]["since"] is not None
    assert by_name["Meena"]["user_id"] == str(manager.id)
    invited = by_name[None]
    assert invited["status"] == "invited" and invited["user_id"] is None
    assert invited["works_as"] == "staff" and invited["since"] is not None
    assert invited["phone_masked"] == "******" + phone(9)[-4:]
    assert by_name["Ravi"]["phone_masked"] == "******" + phone(3)[-4:]
    # Never a number in clear, whoever asks.
    for n in range(2, 10):
        assert phone(n) not in response.text and phone(n)[3:] not in response.text
    as_admin = await api.get(f"/v1/hubs/{SEED_HUB_ID}/staff", headers=boss.headers)
    assert [i["grant_id"] for i in users(as_admin)] == [i["grant_id"] for i in items]
    assert phone(3) not in as_admin.text


async def test_the_hub_staff_list_filters_by_role_and_pages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await person(
        api, google, db_session, 2, name="Meena", roles=(hub_role("hub_manager"),)
    )
    for n, name in ((3, "Ravi"), (4, "Anita"), (5, "Dinesh")):
        await person(api, google, db_session, n, name=name, roles=(hub_role("rider"),))
    path = f"/v1/hubs/{SEED_HUB_ID}/staff"

    riders = await api.get(path, params={"role": "rider"}, headers=manager.headers)
    first = await api.get(path, params={"limit": 2}, headers=manager.headers)
    second = await api.get(
        path, params={"limit": 2, "cursor": first.json()["next_cursor"]}, headers=manager.headers
    )
    bad_role = await api.get(path, params={"role": "wizard"}, headers=manager.headers)
    bad_limit = await api.get(path, params={"limit": 201}, headers=manager.headers)
    bad_cursor = await api.get(path, params={"cursor": "zzz"}, headers=manager.headers)

    assert [i["name"] for i in users(riders)] == ["Anita", "Dinesh", "Ravi"]
    paged = [i["name"] for r in (first, second) for i in users(r)]
    assert paged == ["Meena", "Anita", "Dinesh", "Ravi"]
    assert first.json()["next_cursor"] is not None and second.json()["next_cursor"] is None
    assert bad_role.status_code == bad_limit.status_code == bad_cursor.status_code == 422


async def test_a_revoked_hub_role_leaves_the_hub_list(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    boss = await admin(api, google, db_session)
    keeper = await person(
        api, google, db_session, 2, name="Meena", roles=(hub_role("hub_manager"),)
    )
    leaver = await person(
        api, google, db_session, 3, name="Ravi", roles=(hub_role("franchise_owner"),)
    )
    grant = await grant_of(db_session, leaver.id, "franchise_owner")
    path = f"/v1/hubs/{SEED_HUB_ID}/staff"

    await api.delete(f"/v1/admin/user-roles/{grant.id}", headers=boss.headers)
    after = await api.get(path, headers=keeper.headers)

    assert [i["name"] for i in users(after)] == ["Meena"]


# --- the facade -------------------------------------------------------------------------------


async def test_households_per_society_counts_zero_for_a_society_without_households(
    db_session: AsyncSession,
) -> None:
    other = uuid.uuid4()

    counts = await identity_api.households_per_society(db_session, [SEED_SOCIETY_ID, other])

    assert counts == {SEED_SOCIETY_ID: 0, other: 0}
    assert await identity_api.households_per_society(db_session, []) == {}
