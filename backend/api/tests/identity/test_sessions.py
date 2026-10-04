"""Accounts, sessions and refresh-token rotation (identity/service.py, ADR-0024)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import time_machine
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import LoginSession, RefreshToken, User, UserRole
from gharbasket.modules.identity.service import (
    REUSE_GRACE,
    SESSION_LIFETIME,
    active_grants,
    find_or_create_user,
    hash_refresh_token,
    revoke_all_sessions,
    revoke_session,
    rotate,
    start_session,
)
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.tokens import verify_access_token

from ..conftest import make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]

SETTINGS = make_settings()
T0 = datetime(2026, 10, 3, 10, 0, tzinfo=UTC)
PHONE = "+919800000001"


async def _signed_in(db: AsyncSession, phone: str = PHONE) -> tuple[User, str]:
    user, _ = await find_or_create_user(db, phone)
    issued = await start_session(db, SETTINGS, user, platform="android", now=T0)
    return user, issued.refresh_token


async def _refused(db: AsyncSession, token: str, at: datetime, code: str) -> None:
    with pytest.raises(ProblemError) as caught:
        await rotate(db, SETTINGS, token, now=at)
    assert caught.value.status == 401
    assert caught.value.code == code


# --- sign-up --------------------------------------------------------------------------------


async def test_the_first_sign_in_creates_the_account_with_the_customer_role(
    db_session: AsyncSession,
) -> None:
    user, created = await find_or_create_user(db_session, PHONE)
    again, created_again = await find_or_create_user(db_session, PHONE)

    assert created and not created_again
    assert again.id == user.id
    assert user.id.version == 7
    assert user.locale == "en-IN"
    assert await active_grants(db_session, user.id) == ("customer",)


async def test_only_e164_phone_numbers_are_stored(db_session: AsyncSession) -> None:
    with pytest.raises(IntegrityError, match="ck_users_phone_e164"):
        await find_or_create_user(db_session, "9800000001")


async def test_grants_include_scope_and_skip_unverified_roles(db_session: AsyncSession) -> None:
    user, _ = await find_or_create_user(db_session, PHONE)
    society = uuid.uuid4()
    db_session.add_all(
        [
            UserRole(user_id=user.id, role="captain", scope_type="society", scope_id=society),
            UserRole(
                user_id=user.id,
                role="rider",
                scope_type="hub",
                scope_id=uuid.uuid4(),
                status="kyc_pending",
            ),
        ]
    )
    await db_session.flush()

    assert await active_grants(db_session, user.id) == ("customer", f"captain:{society}")


async def test_the_same_role_cannot_be_granted_twice(db_session: AsyncSession) -> None:
    user, _ = await find_or_create_user(db_session, PHONE)
    db_session.add(UserRole(user_id=user.id, role="customer"))

    with pytest.raises(IntegrityError, match="uq_user_roles"):
        await db_session.flush()


# --- sessions and rotation ------------------------------------------------------------------


async def test_a_session_starts_with_tokens_and_stores_only_a_hash(
    db_session: AsyncSession,
) -> None:
    user, _ = await find_or_create_user(db_session, PHONE)
    issued = await start_session(db_session, SETTINGS, user, platform="web", now=T0)

    stored = (await db_session.execute(select(RefreshToken))).scalar_one()
    with time_machine.travel(T0 + timedelta(minutes=1), tick=False):
        claims = verify_access_token(SETTINGS, issued.access_token)
    assert stored.token_hash == hash_refresh_token(SETTINGS, issued.refresh_token)
    assert issued.refresh_token.encode() not in stored.token_hash
    login = await db_session.get(LoginSession, issued.session_id)
    assert login is not None and login.expires_at == T0 + SESSION_LIFETIME
    assert issued.access_expires_in == 900
    assert (claims.user_id, claims.session_id, claims.roles) == (user.id, login.id, ("customer",))


async def test_refreshing_uses_the_token_and_issues_its_child(db_session: AsyncSession) -> None:
    _, first = await _signed_in(db_session)

    second = await rotate(db_session, SETTINGS, first, now=T0 + timedelta(minutes=14))
    third = await rotate(db_session, SETTINGS, second.refresh_token, now=T0 + timedelta(minutes=28))

    tokens = (
        (await db_session.execute(select(RefreshToken).order_by(RefreshToken.created_at)))
        .scalars()
        .all()
    )
    assert [t.used_at is not None for t in tokens] == [True, True, False]
    assert tokens[1].parent_id == tokens[0].id and tokens[2].parent_id == tokens[1].id
    assert len({first, second.refresh_token, third.refresh_token}) == 3


async def test_an_unknown_token_is_refused(db_session: AsyncSession) -> None:
    await _refused(db_session, "made-up-token", T0, "auth.invalid_token")


async def test_grace_rule_a_lost_response_does_not_log_the_user_out(
    db_session: AsyncSession,
) -> None:
    _, first = await _signed_in(db_session)
    lost = await rotate(db_session, SETTINGS, first, now=T0 + timedelta(minutes=10))

    # The app never received `lost`, so it retries with the old token a moment later.
    retry = await rotate(db_session, SETTINGS, first, now=T0 + timedelta(minutes=10, seconds=40))

    await _refused(db_session, lost.refresh_token, T0 + timedelta(minutes=11), "auth.invalid_token")
    assert await rotate(db_session, SETTINGS, retry.refresh_token, now=T0 + timedelta(minutes=12))
    login = await db_session.get(LoginSession, retry.session_id)
    assert login is not None and login.revoked_at is None


async def test_reusing_a_token_after_its_child_was_used_ends_the_session(
    db_session: AsyncSession,
) -> None:
    _, first = await _signed_in(db_session)
    second = await rotate(db_session, SETTINGS, first, now=T0 + timedelta(minutes=1))
    third = await rotate(db_session, SETTINGS, second.refresh_token, now=T0 + timedelta(minutes=2))

    # Someone replays the very first token: that is a copied token.
    await _refused(db_session, first, T0 + timedelta(minutes=2, seconds=5), "auth.refresh_reused")

    login = await db_session.get(LoginSession, second.session_id, populate_existing=True)
    assert login is not None and login.revoke_reason == "refresh_reused"
    # The legitimate device is signed out too: it must sign in again.
    await _refused(db_session, third.refresh_token, T0 + timedelta(minutes=3), "auth.invalid_token")


async def test_reuse_outside_the_grace_window_ends_the_session(db_session: AsyncSession) -> None:
    _, first = await _signed_in(db_session)
    await rotate(db_session, SETTINGS, first, now=T0)

    late = T0 + REUSE_GRACE + timedelta(seconds=1)
    await _refused(db_session, first, late, "auth.refresh_reused")


async def test_refresh_never_extends_a_session_past_30_days(db_session: AsyncSession) -> None:
    _, first = await _signed_in(db_session)
    near_end = await rotate(
        db_session, SETTINGS, first, now=T0 + SESSION_LIFETIME - timedelta(hours=1)
    )

    await _refused(db_session, near_end.refresh_token, T0 + SESSION_LIFETIME, "auth.invalid_token")


async def test_a_blocked_user_cannot_refresh(db_session: AsyncSession) -> None:
    user, first = await _signed_in(db_session)
    user.status = "blocked"
    await db_session.flush()

    await _refused(db_session, first, T0 + timedelta(minutes=1), "auth.invalid_token")


async def test_logout_and_logout_everywhere(db_session: AsyncSession) -> None:
    user, phone_token = await _signed_in(db_session)
    browser = await start_session(db_session, SETTINGS, user, platform="web", now=T0)
    tablet = await start_session(db_session, SETTINGS, user, platform="android", now=T0)

    await revoke_session(db_session, browser.session_id, now=T0 + timedelta(minutes=1))
    await _refused(
        db_session, browser.refresh_token, T0 + timedelta(minutes=2), "auth.invalid_token"
    )
    assert await rotate(db_session, SETTINGS, tablet.refresh_token, now=T0 + timedelta(minutes=2))

    ended = await revoke_all_sessions(db_session, user.id, now=T0 + timedelta(minutes=3))

    assert len(ended) == 2  # the phone and the tablet; the browser was already signed out
    assert browser.session_id not in ended
    await _refused(db_session, phone_token, T0 + timedelta(minutes=4), "auth.invalid_token")


async def test_refreshed_access_tokens_carry_the_current_roles(db_session: AsyncSession) -> None:
    user, first = await _signed_in(db_session)
    hub = uuid.uuid4()
    db_session.add(UserRole(user_id=user.id, role="packer", scope_type="hub", scope_id=hub))
    await db_session.flush()

    refreshed = await rotate(db_session, SETTINGS, first, now=datetime.now(UTC))

    claims = verify_access_token(SETTINGS, refreshed.access_token)
    assert claims.roles == ("customer", f"packer:{hub}")
    assert claims.user_id == user.id


async def test_two_refreshes_of_one_token_at_the_same_moment_both_succeed(
    database_url: str,
) -> None:
    """Real, committed transactions: the session row lock makes them take turns, and the second
    one falls under the grace rule instead of being treated as theft."""
    engine = create_engine(database_url, pooled=False)
    phone = f"+9198{uuid.uuid4().int % 10**8:08d}"
    try:
        async with AsyncSession(engine, expire_on_commit=False) as setup:
            user, _ = await find_or_create_user(setup, phone)
            first = (await start_session(setup, SETTINGS, user, platform="web")).refresh_token
            await setup.commit()

        async def refresh() -> str:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                issued = await rotate(db, SETTINGS, first)
                await asyncio.sleep(0.1)
                await db.commit()
                return issued.refresh_token

        one, two = await asyncio.gather(refresh(), refresh())

        async with AsyncSession(engine) as check:
            active = await check.execute(
                select(func.count())
                .select_from(RefreshToken)
                .join(LoginSession, LoginSession.id == RefreshToken.session_id)
                .where(
                    LoginSession.user_id == user.id,
                    RefreshToken.used_at.is_(None),
                    RefreshToken.revoked_at.is_(None),
                )
            )
            assert one != two
            assert active.scalar_one() == 1
            await check.execute(delete(User).where(User.id == user.id))
            await check.commit()
    finally:
        await engine.dispose()
