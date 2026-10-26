"""Races of M1 §3.6 on separate connections (so these commit; they clean up after themselves):
two admins removing each other, and a staff role given at the moment the number signs up."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from gharbasket.modules.identity import service, staff_admin
from gharbasket.modules.identity.models import PendingRoleGrant, User, UserRole
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import AuditEntry

pytestmark = [pytest.mark.anyio, pytest.mark.services]


@pytest.fixture
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(database_url, pooled=False)
    yield engine
    await engine.dispose()


@pytest.fixture
async def phones(engine: AsyncEngine) -> AsyncIterator[list[str]]:
    """Numbers of this test's own; afterwards their accounts, roles, grants and audit removed."""
    tag = uuid.uuid4().int % 10_000
    numbers = [f"+9198{tag:04d}{n:04d}" for n in range(3)]
    yield numbers
    async with AsyncSession(engine) as session:
        ids = list(
            (await session.execute(select(User.id).where(User.phone_e164.in_(numbers)))).scalars()
        )
        actors = [*ids, None]
        grants = list(
            (
                await session.execute(
                    select(PendingRoleGrant.id).where(PendingRoleGrant.phone_e164.in_(numbers))
                )
            ).scalars()
        )
        await session.execute(
            delete(AuditEntry).where(
                AuditEntry.entity_id.in_([str(i) for i in [*ids, *grants]])
                | AuditEntry.actor_user_id.in_([a for a in actors if a is not None])
            )
        )
        await session.execute(delete(PendingRoleGrant).where(PendingRoleGrant.id.in_(grants)))
        await session.execute(delete(UserRole).where(UserRole.user_id.in_(ids)))
        await session.execute(delete(User).where(User.id.in_(ids)))
        await session.commit()


def pause_after(session: AsyncSession, calls: int, hook: Callable[[], Awaitable[object]]) -> None:
    """Run `hook` once, right after the session's `calls`-th statement returns."""
    original = session.execute
    seen = 0

    async def execute(*args: Any, **kwargs: Any) -> Any:
        nonlocal seen
        result = await original(*args, **kwargs)
        seen += 1
        if seen == calls:
            await hook()
        return result

    session.execute = execute  # type: ignore[method-assign]


async def an_admin(engine: AsyncEngine, phone_e164: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        user = User(phone_e164=phone_e164)
        session.add(user)
        await session.flush()
        grant = UserRole(
            user_id=user.id, role="admin", status="verified", verified_at=datetime.now(UTC)
        )
        session.add(grant)
        await session.commit()
        return user.id, grant.id


async def test_two_admins_removing_each_other_at_once_leave_one_and_never_deadlock(
    engine: AsyncEngine, phones: list[str]
) -> None:
    first, first_grant = await an_admin(engine, phones[0])
    second, second_grant = await an_admin(engine, phones[1])
    # Both requests read their target before either goes on: a revoke that locked its target row
    # first would now hold one admin row each and wait for the other's (a deadlock).
    both_started = asyncio.Barrier(2)

    async def revoke(grant_id: uuid.UUID, by: uuid.UUID) -> str:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            pause_after(session, 1, both_started.wait)
            try:
                await staff_admin.revoke_grant(session, grant_id, by)
            except ProblemError as exc:
                return exc.code
            await session.commit()
            return "removed"

    outcomes = await asyncio.wait_for(
        asyncio.gather(revoke(second_grant, first), revoke(first_grant, second)), timeout=20
    )

    # The second finds its own admin row gone: its token still says admin, the database not.
    assert sorted(outcomes) == ["identity.not_admin", "removed"]
    async with AsyncSession(engine) as session:
        left = (
            await session.execute(
                select(UserRole.id).where(UserRole.id.in_([first_grant, second_grant]))
            )
        ).scalars()
        assert len(list(left)) == 1


async def test_a_grant_given_while_the_number_signs_up_is_claimed_not_stranded(
    engine: AsyncEngine, phones: list[str]
) -> None:
    boss, _ = await an_admin(engine, phones[0])
    newcomer = phones[1]
    granted_looked = asyncio.Event()

    async def sign_up() -> uuid.UUID:
        async with AsyncSession(engine, expire_on_commit=False) as session:

            async def wait_for_the_grant() -> None:
                await granted_looked.wait()

            # The account is inserted (not committed) before the grant looks for it.
            pause_after(session, 1, wait_for_the_grant)
            user, created = await service.find_or_create_user(session, newcomer)
            assert created
            await session.commit()
            return user.id

    async def grant() -> None:
        async with AsyncSession(engine, expire_on_commit=False) as session:

            async def let_the_sign_up_go_on() -> None:
                granted_looked.set()
                await asyncio.sleep(0.5)  # time for a claim that did not wait for the lock

            # The grant takes the number's lock, then finds no committed account.
            pause_after(session, 2, let_the_sign_up_go_on)
            made = await service.grant_role_or_wait(
                session, phone_e164=newcomer, role="catalog_editor", scope_id=None, granted_by=boss
            )
            assert isinstance(made, PendingRoleGrant)
            await session.commit()

    user_id, _ = await asyncio.wait_for(asyncio.gather(sign_up(), grant()), timeout=20)

    async with AsyncSession(engine) as session:
        roles = (
            await session.execute(select(UserRole.role).where(UserRole.user_id == user_id))
        ).scalars()
        assert sorted(roles) == ["catalog_editor", "customer"]
        waiting = (
            await session.execute(
                select(PendingRoleGrant).where(PendingRoleGrant.phone_e164 == newcomer)
            )
        ).scalars()
        assert list(waiting) == []
