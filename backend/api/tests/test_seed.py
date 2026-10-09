"""`just seed`: synthetic people, societies and households, safe to run again (ADR-0008)."""

import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society
from gharbasket.modules.hub_ops.models import Hub, Vendor
from gharbasket.modules.identity.models import ROLES, Household, HouseholdMember, User, UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners.models import Partner
from gharbasket.platform.db import create_engine
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.platform.permissions import ROLE_SCOPE
from gharbasket.tools.seed import (
    CAPTAINS,
    HOUSEHOLDS,
    PEOPLE,
    SEED_HUB_ID,
    SEED_SOCIETY_ID,
    SOCIETIES,
    seed,
    seed_households,
    seed_partners,
    seed_societies,
)
from gharbasket.tools.seed import main as seed_command

pytestmark = [pytest.mark.anyio, pytest.mark.services]

PHONES = [person.phone for person in PEOPLE]
SLUGS = [society.slug for society in SOCIETIES]


async def _seed_users(session: AsyncSession) -> list[User]:
    users = await session.execute(select(User).where(User.phone_e164.in_(PHONES)))
    return list(users.scalars())


async def _seed_roles(session: AsyncSession) -> list[UserRole]:
    roles = await session.execute(
        select(UserRole).join(User, User.id == UserRole.user_id).where(User.phone_e164.in_(PHONES))
    )
    return list(roles.scalars())


async def _audit_count(session: AsyncSession) -> int:
    users = [str(user.id) for user in await _seed_users(session)]
    count = await session.execute(
        select(func.count()).select_from(AuditEntry).where(AuditEntry.entity_id.in_(users))
    )
    return count.scalar_one()


async def test_seed_creates_one_synthetic_person_for_every_role(db_session: AsyncSession) -> None:
    seeded = await seed(db_session)

    users = await _seed_users(db_session)
    assert len(users) == len(PEOPLE) == len(seeded)
    assert {user.data_source for user in users} == {"synthetic"}
    assert all(user.name for user in users)

    roles = await _seed_roles(db_session)
    assert {role.role for role in roles} == set(ROLES)
    assert {role.status for role in roles} == {"verified"}
    for role in roles:
        assert role.scope_type == ROLE_SCOPE[role.role]
    scopes = {role.role: role.scope_id for role in roles}
    assert scopes["captain"] == SEED_SOCIETY_ID
    assert scopes["hub_manager"] == SEED_HUB_ID


async def test_seeding_again_changes_nothing(db_session: AsyncSession) -> None:
    await seed(db_session)
    roles = len(await _seed_roles(db_session))
    audits = await _audit_count(db_session)

    again = await seed(db_session)

    assert not any(person.created for person in again)
    assert len(await _seed_roles(db_session)) == roles
    assert await _audit_count(db_session) == audits


async def test_a_seed_phone_signed_in_by_hand_becomes_synthetic(db_session: AsyncSession) -> None:
    manual, _ = await find_or_create_user(db_session, PHONES[0])
    assert manual.data_source == "real"

    await seed(db_session)

    await db_session.refresh(manual)
    assert manual.data_source == "synthetic"
    assert manual.name == PEOPLE[0].name


async def test_seed_societies_are_synthetic_and_seeding_again_adds_none(
    db_session: AsyncSession,
) -> None:
    first = await seed_societies(db_session)
    again = await seed_societies(db_session)

    societies = list(
        (await db_session.execute(select(Society).where(Society.slug.in_(SLUGS)))).scalars()
    )
    assert [created for _, created in first] == [True] * len(SOCIETIES)
    assert not any(created for _, created in again)
    assert len(societies) == len(SOCIETIES)
    assert {society.data_source for society in societies} == {"synthetic"}
    # The captain's role scope (above) points at a real seed society.
    assert SEED_SOCIETY_ID in {society.id for society in societies}


async def test_seed_households_onboard_five_customers_once(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed_societies(db_session)

    first = await seed_households(db_session)
    again = await seed_households(db_session)

    assert [created for _, created in first] == [True] * len(HOUSEHOLDS)
    assert not any(created for _, created in again)
    households = list((await db_session.execute(select(Household))).scalars())
    assert len(households) == len(HOUSEHOLDS)
    assert {household.data_source for household in households} == {"synthetic"}
    # Ramesh gave health consent, so his household keeps its health tags.
    tags = (await db_session.execute(select(HouseholdMember.health_tags))).scalars().all()
    assert ["diabetes", "low_oil"] in tags


@pytest.fixture
def local_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    # Both, so a developer's local .env cannot point the command somewhere else.
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_URL_DIRECT", database_url)
    yield database_url


async def _remove_seed_rows(database_url: str) -> tuple[int, int]:
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine) as cleanup:
            user_ids = [user.id for user in await _seed_users(cleanup)]
            users = [str(user_id) for user_id in user_ids]
            await cleanup.execute(delete(AuditEntry).where(AuditEntry.entity_id.in_(users)))
            await cleanup.execute(
                delete(OutboxEvent).where(
                    OutboxEvent.event_type == "identity.household_created",
                    OutboxEvent.payload["data_source"].astext == "synthetic",
                )
            )
            await cleanup.execute(
                delete(OutboxEvent).where(
                    OutboxEvent.event_type == "partners.verified",
                    OutboxEvent.payload["user_id"].astext.in_(users),
                )
            )
            await cleanup.execute(delete(Partner).where(Partner.user_id.in_(user_ids)))
            # Households, members, addresses and consents go with their owners.
            await cleanup.execute(delete(User).where(User.phone_e164.in_(PHONES)))
            societies = await cleanup.execute(delete(Society).where(Society.slug.in_(SLUGS)))
            await cleanup.execute(
                delete(OutboxEvent).where(
                    OutboxEvent.event_type.like("hub_ops.hub_%"),
                    OutboxEvent.payload["hub_id"].astext == str(SEED_HUB_ID),
                )
            )
            await cleanup.execute(delete(Vendor).where(Vendor.hub_id == SEED_HUB_ID))
            await cleanup.execute(delete(Hub).where(Hub.id == SEED_HUB_ID))
            await cleanup.commit()
            return len(users), societies.rowcount  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        await engine.dispose()


def test_the_seed_command_fills_the_local_database(
    local_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    try:
        assert seed_command([]) == 0
        out = capsys.readouterr().out
        assert f"hub kharghar ({SEED_HUB_ID}) new" in out
        assert f"{len(PEOPLE)} synthetic people" in out
        assert f"{len(SOCIETIES)} synthetic societies" in out
        assert f"{len(HOUSEHOLDS)} synthetic households" in out
        assert f"{len(CAPTAINS)} synthetic captain partners" in out
    finally:
        # The command commits for real, so its rows are removed here (and counted).
        removed = asyncio.run(_remove_seed_rows(local_database))
        assert removed == (len(PEOPLE), len(SOCIETIES))


def test_the_seed_command_refuses_a_shared_database(monkeypatch: pytest.MonkeyPatch) -> None:
    neon = "postgresql://app:pw@ep-x.ap-southeast-1.aws.neon.tech/db"
    monkeypatch.setenv("DATABASE_URL", neon)
    monkeypatch.setenv("DATABASE_URL_DIRECT", neon)

    with pytest.raises(SystemExit) as exited:
        seed_command([])

    assert exited.value.code == 2


async def test_seed_captains_are_verified_partners_where_they_live(
    db_session: AsyncSession,
) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)

    first = await seed_partners(db_session)
    again = await seed_partners(db_session)

    assert [created for _, created in first] == [True] * len(CAPTAINS)
    assert not any(created for _, created in again)
    partners = list((await db_session.execute(select(Partner))).scalars())
    assert {(p.kind, p.status, p.data_source, p.hub_id) for p in partners} == {
        ("captain", "active", "synthetic", SEED_HUB_ID)
    }
    users = {user.phone_e164: user.id for user in await _seed_users(db_session)}
    homes = {
        h.owner_user_id: h.society_id
        for h in (await db_session.execute(select(Household))).scalars()
    }
    live = {s.id for s in SOCIETIES if s.status == "live"}
    assert {society for _, society, _ in CAPTAINS} == live
    for phone, society_id, _ in CAPTAINS:
        assert homes[users[phone]] == society_id  # a captain lives in her society (M9 §3.2)
    # Each is verified once, with the society she asked for (ADR-0026 §4).
    verified = (
        (
            await db_session.execute(
                select(OutboxEvent).where(OutboxEvent.event_type == "partners.verified")
            )
        )
        .scalars()
        .all()
    )
    assert sorted(
        (e.payload["user_id"], e.payload["requested_society_id"]) for e in verified
    ) == sorted((str(users[phone]), str(society)) for phone, society, _ in CAPTAINS)
