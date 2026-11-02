"""What a `--write-db` run must not do to a shared database (staging) beyond its own households:
notice another household's draft, unlock a society, adopt a real account on a simulator phone,
or leave its subscriptions to staging's weekly job (PR #480 review)."""

import re
import uuid
from datetime import date, timedelta

import pytest
from gharbasket_sim import config as cohorts
from gharbasket_sim import staging
from gharbasket_sim import through_modules as through
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample
from gharbasket_sim.run import RealUserOnSimulatorPhone, populate, simulate_world
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community.models import Society
from gharbasket.modules.identity.models import Household, User
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.subscriptions import service as subs_service
from gharbasket.modules.subscriptions.models import Subscription, WeeklyCycle
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import (
    HOUSEHOLDS,
    SEED_HUB_ID,
    seed_catalog,
    seed_delivery,
    seed_households,
    seed_societies,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services]

MONDAY = date(2026, 10, 5)
TUESDAY = MONDAY + timedelta(days=1)  # the seed societies' delivery day
REAL_PHONE = next(iter(HOUSEHOLDS))  # Priya, Sunrise Towers; made "real" below


async def seeded(db: AsyncSession) -> None:
    await seed_societies(db)
    await seed_households(db)
    await seed_catalog(db)
    await seed_delivery(db)


async def real_household(db: AsyncSession) -> uuid.UUID:
    user = await db.scalar(select(User).where(User.phone_e164 == REAL_PHONE))
    assert user is not None
    user.data_source = "real"
    household = await db.scalar(select(Household).where(Household.owner_user_id == user.id))
    assert household is not None
    household.data_source = "real"
    await subs_service.subscribe(db, household.id, plan_code="autopilot", payment_mode="wallet")
    await db.flush()
    return household.id


async def real_draft(db: AsyncSession, household_id: uuid.UUID) -> WeeklyCycle:
    """The draft staging's own job made for a real household, not yet shown to it."""
    plan_catalog = await catalog_api.plan_catalog(db, SEED_HUB_ID)
    async with through.ml_client() as http:
        with through.simulated_time(through.ist(TUESDAY - timedelta(days=3), 18, 0)):
            generated = await subs_service.generate_cycle(
                db,
                http,
                through.app_settings("rules"),
                household_id=household_id,
                delivery_date=TUESDAY,
                catalog=plan_catalog,
            )
    assert generated is not None and generated.cycle.status == "draft"
    return generated.cycle


async def test_a_real_households_draft_in_the_window_is_left_alone(
    db_session: AsyncSession,
) -> None:
    await seeded(db_session)
    draft = await real_draft(db_session, await real_household(db_session))

    run = await simulate_world(
        db_session, seed=7, weeks=1, start=MONDAY, planner="rules", adoption=0.005, write_db=True
    )

    assert run.rows.outcomes
    await db_session.refresh(draft)
    assert (draft.status, draft.notice_sent_at) == ("draft", None)
    told = (
        await db_session.execute(
            select(OutboxEvent.payload).where(OutboxEvent.event_type == "subscriptions.box_ready")
        )
    ).scalars()
    assert str(draft.id) not in {payload["cycle_id"] for payload in told}


async def test_subscriptions_are_paused_after_a_staging_run_and_resumed_by_the_next(
    db_session: AsyncSession,
) -> None:
    await seeded(db_session)
    real = await real_household(db_session)
    await real_draft(db_session, real)

    first = await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=MONDAY,
        planner="rules",
        adoption=0.005,
        write_db=True,
        pause_after=True,
    )
    ids = [first.created[h.key][0] for h in first.households]
    subs = (
        await db_session.execute(select(Subscription).where(Subscription.household_id.in_(ids)))
    ).scalars()
    assert {(s.status, s.paused_until) for s in subs} == {("paused", through.PAUSED_UNTIL)}
    mine = await subs_service.subscription_of(db_session, real)
    assert mine is not None and (mine.status, mine.paused_until) == ("active", None)
    # Staging's weekly job skips a paused subscription.
    plan_catalog = await catalog_api.plan_catalog(db_session, SEED_HUB_ID)
    async with through.ml_client() as http:
        skipped = await subs_service.generate_cycle(
            db_session,
            http,
            through.app_settings("rules"),
            household_id=ids[0],
            delivery_date=TUESDAY + timedelta(weeks=1),
            catalog=plan_catalog,
        )
    assert skipped is None

    # The weeks the simulator replayed count as taken; the real household's draft does not.
    weeks = (await db_session.execute(text(staging.SIMULATED_WEEKS))).scalars().all()
    assert weeks == [MONDAY]

    second = await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=MONDAY + timedelta(weeks=1),
        planner="rules",
        adoption=0.005,
        write_db=True,
        pause_after=True,
    )
    assert all(o["cycle_id"] for o in second.rows.outcomes)  # resumed for the run
    weeks = (await db_session.execute(text(staging.SIMULATED_WEEKS))).scalars().all()
    assert weeks == [MONDAY, MONDAY + timedelta(weeks=1)]
    subs = (
        await db_session.execute(select(Subscription).where(Subscription.household_id.in_(ids)))
    ).scalars()
    assert {s.status for s in subs} == {"paused"}


async def test_a_run_that_would_unlock_a_society_is_caught(db_session: AsyncSession) -> None:
    await seeded(db_session)

    plan = await staging.unlock_plan(db_session, 0.015)

    assert plan and not any(u.would_go_live for u in plan)
    crossing = next(u for u in plan if u.new > 0)
    await db_session.execute(
        update(Society)
        .where(Society.slug == crossing.slug)
        .values(unlock_threshold=crossing.joined + crossing.new)
    )
    again = {u.slug: u for u in await staging.unlock_plan(db_session, 0.015)}
    assert again[crossing.slug].would_go_live
    # Households already onboarded are not new.
    assert not staging.Unlock("s", 9, 0, 9).would_go_live


def test_the_unlock_command_exits_5_when_a_society_would_go_live(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rows = [staging.Unlock("green-valley", 13, 2, 15), staging.Unlock("shree-ganesh", 1, 1, 15)]
    monkeypatch.setattr(staging, "database_url", lambda: "postgresql://unused@localhost/x")
    monkeypatch.setattr(staging, "unlocking", lambda url, adoption: rows)

    assert staging.main(["unlock", "--adoption", "0.015"]) == staging.EXIT_WOULD_UNLOCK
    out = capsys.readouterr()
    assert "green-valley: 13 joined + 2 new of 15: WOULD GO LIVE" in out.out
    assert "shree-ganesh: 1 joined + 1 new of 15: stays unlocking" in out.out
    assert "refused" in out.err
    rows.pop(0)
    assert staging.main(["unlock", "--adoption", "0.015"]) == 0


def test_planned_phones_follow_the_sample(catalog: Catalog) -> None:
    households = sample(7, cohorts.load(), catalog, 0.015)
    planned = staging.planned_phones(0.015)
    assert sorted(p for ps in planned.values() for p in ps) == sorted(h.phone for h in households)
    for h in households:
        assert h.phone in planned[uuid.UUID(h.society_id)]
    assert staging.expected_households(0.015) == 13


async def test_a_real_account_on_a_simulator_phone_is_refused_on_a_shared_database(
    db_session: AsyncSession, catalog: Catalog
) -> None:
    await seed_societies(db_session)
    households = sample(7, cohorts.load(), catalog, 0.005)
    stranger, _ = await find_or_create_user(db_session, households[-1].phone)
    stranger.name = "Someone real"
    await db_session.flush()

    with pytest.raises(RealUserOnSimulatorPhone, match=re.escape(households[-1].phone)):
        await populate(db_session, households, adopt_real=False)

    await db_session.refresh(stranger)
    assert (stranger.name, stranger.data_source) == ("Someone real", "real")
    # Locally (the default) the account becomes the simulator's, as before.
    await populate(db_session, households)
    await db_session.refresh(stranger)
    assert stranger.data_source == "synthetic"
