"""identity.api.household_facts: M12's cohort rows (M12 §3.1), with no personal field."""

import dataclasses
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import Household, User

from ..catalog.conftest import world  # noqa: F401

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]
IST = ZoneInfo("Asia/Kolkata")


async def test_household_facts_are_ids_dates_and_the_account_status(
    db_session: AsyncSession,
) -> None:
    facts = await identity_api.household_facts(db_session)
    today = datetime.now(IST).date()

    assert len(facts) >= 3
    assert {(f.created_on, f.status, f.data_source) for f in facts} == {
        (today, "active", "synthetic")
    }
    assert [f.household_id for f in facts] == sorted(f.household_id for f in facts)
    assert {field.name for field in dataclasses.fields(identity_api.HouseholdFact)} == {
        "household_id",
        "hub_id",
        "society_id",
        "created_on",
        "status",
        "data_source",
    }

    one = facts[0]
    owner = (await db_session.get(Household, one.household_id)).owner_user_id  # pyright: ignore[reportOptionalMemberAccess]
    await db_session.execute(update(User).where(User.id == owner).values(status="deleting"))
    [found] = await identity_api.household_facts(db_session, ids=[one.household_id])
    assert found == dataclasses.replace(one, status="deleting")
    assert await identity_api.household_facts(db_session, ids=[]) == []
    later = today + timedelta(days=1)
    assert await identity_api.household_facts(db_session, created_from=later) == []
    assert len(await identity_api.household_facts(db_session, created_from=today)) == len(facts)
