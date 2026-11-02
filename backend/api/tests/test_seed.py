"""`just seed`: synthetic people, societies and households, safe to run again (ADR-0008)."""

import asyncio
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import func, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Category, Product
from gharbasket.modules.community.models import Captain, Society
from gharbasket.modules.hub_ops.models import Hub
from gharbasket.modules.identity.models import ROLES, Household, HouseholdMember, User, UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners.models import Partner, TrainingModule
from gharbasket.platform.db import REMOTE_SEED_CONFIRMATION, create_engine
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.platform.permissions import ROLE_SCOPE
from gharbasket.tools.seed import (
    CAPTAINS,
    HOUSEHOLDS,
    PEOPLE,
    SEED_HUB_ID,
    SEED_SOCIETY_ID,
    SOCIETIES,
    Report,
    seed,
    seed_catalog,
    seed_households,
    seed_hubs,
    seed_partners,
    seed_societies,
    seed_trainings,
)
from gharbasket.tools.seed import main as seed_command

from .conftest import alembic_config, run_on_server

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
    # Every role but the captain's, which follows from M9's appointment (seed_partners, below).
    assert {role.role for role in roles} == set(ROLES) - {"captain"}
    assert {role.status for role in roles} == {"verified"}
    for role in roles:
        assert role.scope_type == ROLE_SCOPE[role.role]
    scopes = {role.role: role.scope_id for role in roles}
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
    # The seed captains' society (seed_partners, below) is a real seed society.
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
def seeded_database(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A throwaway database for the command: it commits for real (people, societies, the whole
    catalog, a delivery, and the notifications its relay sends), which would leak into the
    other tests' database, and under pytest-xdist into whichever test runs next on that worker."""
    name = f"{make_url(database_url).database}_seed"
    url = make_url(database_url).set(database=name).render_as_string(hide_password=False)
    run_on_server(database_url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(database_url, f"CREATE DATABASE {name}")
    try:
        command.upgrade(alembic_config(url), "head")
        # Both, so a developer's local .env cannot point the command somewhere else.
        monkeypatch.setenv("DATABASE_URL", url)
        monkeypatch.setenv("DATABASE_URL_DIRECT", url)
        yield url
    finally:
        run_on_server(database_url, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


async def _count_seed_rows(database_url: str) -> tuple[int, int]:
    engine = create_engine(database_url, pooled=False)
    try:
        async with AsyncSession(engine) as session:
            users = await session.scalar(
                select(func.count()).select_from(User).where(User.phone_e164.in_(PHONES))
            )
            societies = await session.scalar(
                select(func.count()).select_from(Society).where(Society.slug.in_(SLUGS))
            )
            return users or 0, societies or 0
    finally:
        await engine.dispose()


def test_the_seed_command_fills_the_database(
    seeded_database: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert seed_command([]) == 0
    out = capsys.readouterr().out
    assert f"hub kharghar ({SEED_HUB_ID}) new" in out
    assert f"{len(PEOPLE)} synthetic people" in out
    assert f"{len(SOCIETIES)} synthetic societies" in out
    assert f"{len(HOUSEHOLDS)} synthetic households" in out
    assert f"{len(CAPTAINS)} synthetic captain partners" in out
    assert "6 partner training modules (placeholders)" in out
    # It committed: the rows are there for a new connection.
    assert asyncio.run(_count_seed_rows(seeded_database)) == (len(PEOPLE), len(SOCIETIES))


def test_the_seed_command_refuses_a_shared_database(monkeypatch: pytest.MonkeyPatch) -> None:
    neon = "postgresql://app:pw@ep-x.ap-southeast-1.aws.neon.tech/db"
    monkeypatch.setenv("DATABASE_URL", neon)
    monkeypatch.setenv("DATABASE_URL_DIRECT", neon)

    with pytest.raises(SystemExit) as exited:
        seed_command([])

    assert exited.value.code == 2


NEON_HOST = "ep-x.ap-southeast-1.aws.neon.tech"


@pytest.fixture
def staging_seed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool, bool]]:
    """DATABASE_URL on a Neon host, and `run` replaced so nothing connects: the calls it got."""
    neon = f"postgresql://app:pw@{NEON_HOST}/db"
    monkeypatch.setenv("DATABASE_URL", neon)
    monkeypatch.setenv("DATABASE_URL_DIRECT", neon)
    monkeypatch.delenv(REMOTE_SEED_CONFIRMATION, raising=False)
    calls: list[tuple[str, bool, bool]] = []

    async def fake_run(url: str, *, refresh: bool = False, keep_existing: bool = False) -> Report:
        calls.append((url, refresh, keep_existing))
        return Report(people=[], societies=[], households=[], partners=[], products=[])

    monkeypatch.setattr("gharbasket.tools.seed.run", fake_run)
    return calls


@pytest.mark.parametrize(
    ("argv", "confirmation"),
    [
        (["--allow-remote"], None),  # the flag without the host
        (["--allow-remote"], "ep-other.neon.tech"),  # the host of another database
        ([], NEON_HOST),  # the host without the flag
        (["--allow-remote", "--refresh"], NEON_HOST),  # never a refresh there
    ],
)
def test_the_seed_command_refuses_staging_without_both_confirmations(
    staging_seed: list[tuple[str, bool, bool]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    confirmation: str | None,
) -> None:
    if confirmation is not None:
        monkeypatch.setenv(REMOTE_SEED_CONFIRMATION, confirmation)

    with pytest.raises(SystemExit) as exited:
        seed_command(argv)

    assert exited.value.code == 2
    assert staging_seed == []
    assert "pw@" not in capsys.readouterr().err


def test_the_seed_command_only_adds_on_staging_with_both_confirmations(
    staging_seed: list[tuple[str, bool, bool]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(REMOTE_SEED_CONFIRMATION, NEON_HOST)

    assert seed_command(["--allow-remote"]) == 0

    assert staging_seed == [(f"postgresql://app:pw@{NEON_HOST}/db", False, True)]
    out = capsys.readouterr().out
    assert f"seeding the shared database at {NEON_HOST}: adds only" in out
    assert "pw@" not in out


async def test_keep_existing_never_gives_back_a_role_revoked_in_the_console(
    db_session: AsyncSession,
) -> None:
    await seed(db_session)
    neha = next(p for p in PEOPLE if ("admin", None) in p.roles)
    user = await db_session.scalar(select(User).where(User.phone_e164 == neha.phone))
    assert user is not None
    revoked = await db_session.scalar(
        select(UserRole).where(UserRole.user_id == user.id, UserRole.role == "admin")
    )
    assert revoked is not None
    await db_session.delete(revoked)  # what the console's revoke does (staff_admin.revoke_grant)
    await db_session.flush()

    await seed(db_session, keep_existing=True)

    roles = await db_session.scalars(select(UserRole.role).where(UserRole.user_id == user.id))
    assert "admin" not in roles.all()
    # Locally the seed puts its people back as they were, roles included.
    await seed(db_session)
    roles = await db_session.scalars(select(UserRole.role).where(UserRole.user_id == user.id))
    assert "admin" in roles.all()


async def test_keep_existing_adds_what_is_missing_and_keeps_every_existing_row(
    db_session: AsyncSession,
) -> None:
    await seed_hubs(db_session)
    await seed(db_session)
    await seed_societies(db_session)
    await seed_catalog(db_session)
    await seed_trainings(db_session)
    # Edited in the console since (none of these has `updated_by`), and one society is new.
    hub = await db_session.get(Hub, SEED_HUB_ID)
    assert hub is not None
    hub.name = "Kharghar (edited)"
    person = (await _seed_users(db_session))[0]
    person.name = "Edited"
    edited, missing = SLUGS[0], SLUGS[-1]
    society = await db_session.scalar(select(Society).where(Society.slug == edited))
    assert society is not None
    society.name = "Edited society"
    await db_session.delete(await db_session.scalar(select(Society).where(Society.slug == missing)))
    category = await db_session.scalar(select(Category).order_by(Category.slug).limit(1))
    assert category is not None
    category.names = {**category.names, "en": "Edited"}
    module = await db_session.scalar(select(TrainingModule).limit(1))
    assert module is not None
    module.minutes = 99
    await db_session.flush()
    products = await db_session.scalar(select(func.count()).select_from(Product))

    await seed_hubs(db_session, keep_existing=True)
    await seed(db_session, keep_existing=True)
    societies = dict(await seed_societies(db_session, keep_existing=True))
    added = await seed_catalog(db_session, keep_existing=True)
    trainings = await seed_trainings(db_session, keep_existing=True)

    for row in (hub, person, society, category, module):
        await db_session.refresh(row)
    assert hub.name == "Kharghar (edited)"
    assert person.name == "Edited"
    assert society.name == "Edited society"
    assert category.names["en"] == "Edited"
    assert module.minutes == 99
    assert societies[missing] is True and societies[edited] is False
    assert not any(created for _, created in added)
    assert not any(created for _, created in trainings)
    assert await db_session.scalar(select(func.count()).select_from(Product)) == products
    with pytest.raises(ValueError, match="keep_existing"):
        await seed_catalog(db_session, refresh=True, keep_existing=True)


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


async def test_seed_captains_are_appointed_and_get_the_captain_role(
    db_session: AsyncSession,
) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_partners(db_session)

    while (await relay(db_session)).sent:  # what `just seed` does once it has committed
        pass

    users = {user.phone_e164: user.id for user in await _seed_users(db_session)}
    captains = {
        (role.user_id, role.scope_id)
        for role in await _seed_roles(db_session)
        if role.role == "captain" and role.status == "verified"
    }
    assert captains == {(users[phone], society) for phone, society, _ in CAPTAINS}
    appointed = (await db_session.execute(select(Captain))).scalars().all()
    assert {(c.user_id, c.society_id, c.appointed_by) for c in appointed} == {
        (users[phone], society, "auto") for phone, society, _ in CAPTAINS
    }
