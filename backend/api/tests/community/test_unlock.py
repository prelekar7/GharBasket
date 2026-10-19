"""Society unlock (M9 §3.6): the first captain starts the count, the household that reaches the
threshold makes the society live once, nothing moves it back, and the weekly job prices the
threshold from M10's stop cost when it can."""

import copy
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import jobs, unlock
from gharbasket.modules.community.models import Society
from gharbasket.modules.identity.households import create_household
from gharbasket.modules.identity.models import Household
from gharbasket.modules.identity.schemas import HouseholdIn
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import service as partners_service
from gharbasket.platform.jobs import JobContext
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import HOUSEHOLDS, SEED_HUB_ID, SEED_SOCIETY_ID, SOCIETIES

from ..conftest import make_settings
from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .conftest import deliver

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("captain_world")]

SUNITA = "+919800001002"  # lives in Green Valley Heights
VIKRAM = "+919800001008"  # hub manager of the seed hub
NEHA = "+919800001011"  # admin
GREEN_VALLEY = next(s.id for s in SOCIETIES if s.slug == "green-valley-heights-kharghar")
SHREE_GANESH = next(s.id for s in SOCIETIES if s.slug == "shree-ganesh-chs-kharghar")
PANCHAVATI = next(s.id for s in SOCIETIES if s.slug == "panchavati-residency-kamothe")
LIVE_KEYS = {"society_id", "hub_id", "joined", "threshold", "live_at"}


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _move_in(db: AsyncSession, n: int, society_id: uuid.UUID = GREEN_VALLEY) -> None:
    """A new household in the society (Sunita's onboarding with another flat), delivered."""
    user, _ = await find_or_create_user(db, f"+9198000077{n:02d}")
    body = copy.deepcopy(HOUSEHOLDS[SUNITA])
    body["society_id"] = str(society_id)
    body["address"] = {"tower_id": "b", "flat": f"B-{n}", "location": None}
    await create_household(db, user.id, HouseholdIn.model_validate(body))
    await deliver(db)


async def _society(db: AsyncSession, society_id: uuid.UUID) -> Society:
    society = await db.get(Society, society_id, populate_existing=True)
    assert society is not None
    return society


async def _live_events(db: AsyncSession, society_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(
            OutboxEvent.event_type == community_api.SOCIETY_LIVE,
            OutboxEvent.payload["society_id"].astext == str(society_id),
        )
    )
    return [event.payload for event in rows.scalars()]


async def _set_threshold(db: AsyncSession, society_id: uuid.UUID, threshold: int) -> None:
    society = await _society(db, society_id)
    society.unlock_threshold = threshold
    await db.flush()


async def test_the_household_that_reaches_the_threshold_makes_the_society_live_once(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    await _set_threshold(db_session, GREEN_VALLEY, 3)  # Sunita's household is the first
    await _move_in(db_session, 1)
    assert (await _society(db_session, GREEN_VALLEY)).status == "unlocking"

    await _move_in(db_session, 2)

    society = await _society(db_session, GREEN_VALLEY)
    assert society.status == "live" and society.live_at is not None
    (event,) = await _live_events(db_session, GREEN_VALLEY)
    assert set(event) == LIVE_KEYS
    assert (event["joined"], event["threshold"], event["hub_id"]) == (3, 3, str(SEED_HUB_ID))
    api = clients()
    unlock_view = await api.get(
        f"/v1/societies/{GREEN_VALLEY}/unlock", headers=await _as(api, google, SUNITA)
    )
    body = unlock_view.json()
    assert datetime.fromisoformat(body.pop("live_at")) == society.live_at
    assert body == {
        "society_id": str(GREEN_VALLEY),
        "status": "live",
        "joined": 3,
        "threshold": 3,
        "threshold_source": "default",
        "box_fee_paise": 0,
    }


async def test_a_live_society_stays_live_and_publishes_once(db_session: AsyncSession) -> None:
    await _set_threshold(db_session, GREEN_VALLEY, 2)
    await _move_in(db_session, 1)
    await _move_in(db_session, 2)  # another household after live: nothing new
    assert len(await _live_events(db_session, GREEN_VALLEY)) == 1

    # Households leave: it stays live, and its delivery stays free.
    await db_session.execute(delete(Household).where(Household.society_id == GREEN_VALLEY))
    await _set_threshold(db_session, GREEN_VALLEY, 50)
    for payload in ({"society_id": str(GREEN_VALLEY)}, {"society_id": "x"}, {}):
        await unlock.on_household_created(
            db_session, OutboxEvent(event_type="identity.household_created", payload=payload)
        )
    society = await _society(db_session, GREEN_VALLEY)
    assert society.status == "live"
    assert unlock.box_fee_paise(society) == 0


async def test_the_first_captain_moves_a_prospect_society_to_unlocking(
    db_session: AsyncSession,
) -> None:
    # Sunita lives in Green Valley; make it a prospect, then she is verified as its captain.
    (await _society(db_session, GREEN_VALLEY)).status = "prospect"
    await db_session.flush()
    user, _ = await find_or_create_user(db_session, SUNITA)

    await partners_service.verify(
        db_session,
        user_id=user.id,
        kind="captain",
        hub_id=SEED_HUB_ID,
        display_name="Sunita tai",
        requested_society_id=GREEN_VALLEY,
    )
    await deliver(db_session)

    society = await _society(db_session, GREEN_VALLEY)
    assert society.status == "unlocking" and society.live_at is None
    assert unlock.box_fee_paise(society) == unlock.PRE_LIVE_BOX_FEE_PAISE == 3_000


async def test_the_hub_pins_the_threshold_and_pauses_and_resumes_without_a_second_event(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    path = f"/v1/admin/societies/{GREEN_VALLEY}"

    pinned = await api.patch(
        path, json={"unlock_threshold": 30, "unlock_threshold_pinned": True}, headers=vikram
    )

    assert pinned.status_code == 200, pinned.text
    assert pinned.json() | {"live_at": None} == {
        "society_id": str(GREEN_VALLEY),
        "status": "unlocking",
        "joined": 1,
        "threshold": 30,
        "live_at": None,
        "threshold_source": "hub",
        "box_fee_paise": 3_000,
        "unlock_threshold_pinned": True,
    }
    # A threshold at or below joined makes it live at once.
    went_live = await api.patch(path, json={"unlock_threshold": 1}, headers=vikram)
    assert went_live.json()["status"] == "live"
    for status in ("paused", "live"):
        changed = await api.patch(path, json={"status": status}, headers=vikram)
        assert changed.json()["status"] == status
        assert changed.json()["box_fee_paise"] == 0  # live_at decides, not status
    assert len(await _live_events(db_session, GREEN_VALLEY)) == 1


async def test_only_hub_staff_change_a_society(
    db_session: AsyncSession, clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    await db_session.commit()
    customer = await _as(api, google, SUNITA)

    refused = await api.patch(
        f"/v1/admin/societies/{GREEN_VALLEY}", json={"status": "paused"}, headers=customer
    )
    unknown = await api.patch(
        f"/v1/admin/societies/{uuid.uuid4()}",
        json={"status": "paused"},
        headers=await _as(api, google, NEHA),
    )
    invalid = await api.patch(
        f"/v1/admin/societies/{GREEN_VALLEY}",
        json={"status": "prospect"},
        headers=await _as(api, google, NEHA),
    )

    assert refused.status_code == 403
    assert unknown.json()["code"] == "community.society_not_found"
    assert invalid.status_code == 422
    assert (await _society(db_session, GREEN_VALLEY)).status == "unlocking"


def _ctx(db: AsyncSession) -> JobContext:
    return JobContext(session=db, scheduled_for=datetime.now(UTC), settings=make_settings())


async def test_without_a_stop_cost_the_job_keeps_every_threshold(db_session: AsyncSession) -> None:
    before = {s.id: (s.unlock_threshold, s.threshold_source) for s in await _all(db_session)}

    counts = await jobs.recompute_unlock_thresholds(_ctx(db_session))

    after = {s.id: (s.unlock_threshold, s.threshold_source) for s in await _all(db_session)}
    assert after == before
    # Two live societies are skipped; the four others keep their threshold.
    assert counts == {"priced": 0, "kept": 4, "skipped": 2, "went_live": 0}


async def test_the_job_prices_thresholds_from_the_stop_cost(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    costs = {GREEN_VALLEY: 100_000, SHREE_GANESH: 9_000, PANCHAVATI: 1_000_000}

    async def stop_cost(_session: AsyncSession, society_id: uuid.UUID) -> int | None:
        return costs.get(society_id)

    monkeypatch.setattr(unlock, "stop_cost", stop_cost)
    sunrise = await _society(db_session, SEED_SOCIETY_ID)
    sunrise_threshold = sunrise.unlock_threshold
    panchavati = await _society(db_session, PANCHAVATI)
    panchavati.unlock_threshold_pinned = True
    await db_session.flush()

    counts = await jobs.recompute_unlock_thresholds(_ctx(db_session))

    green, shree = (
        await _society(db_session, GREEN_VALLEY),
        await _society(db_session, SHREE_GANESH),
    )
    assert (green.unlock_threshold, green.threshold_source) == (25, "model")  # ₹1,000 ÷ ₹40
    assert (shree.unlock_threshold, shree.threshold_source) == (10, "model")  # at least 10
    assert green.threshold_computed_at is not None
    assert (await _society(db_session, PANCHAVATI)).unlock_threshold == 20  # pinned
    assert (await _society(db_session, SEED_SOCIETY_ID)).unlock_threshold == sunrise_threshold
    assert counts["priced"] == 2
    assert unlock.threshold_for(1_000_000) == 60  # at most 60


async def test_a_lower_priced_threshold_makes_the_society_live_in_the_job(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for n in range(1, 10):  # ten households with Sunita's
        await _move_in(db_session, n)

    async def stop_cost(_session: AsyncSession, society_id: uuid.UUID) -> int | None:
        return 40_000 if society_id == GREEN_VALLEY else None  # N = 10

    monkeypatch.setattr(unlock, "stop_cost", stop_cost)
    counts = await jobs.recompute_unlock_thresholds(_ctx(db_session))

    assert counts["went_live"] == 1
    assert (await _society(db_session, GREEN_VALLEY)).status == "live"
    (event,) = await _live_events(db_session, GREEN_VALLEY)
    assert (event["joined"], event["threshold"]) == (10, 10)


async def test_a_household_that_moves_in_counts_and_bad_payloads_change_nothing(
    db_session: AsyncSession,
) -> None:
    await _set_threshold(db_session, GREEN_VALLEY, 1)
    for payload in (
        {},
        {"household_id": "x", "changed": ["society_id"]},
        {"household_id": str(uuid.uuid4()), "changed": "society_id"},
    ):
        await unlock.on_household_updated(
            db_session, OutboxEvent(event_type="identity.household_updated", payload=payload)
        )
    assert (await _society(db_session, GREEN_VALLEY)).status == "unlocking"
    sunita, _ = await find_or_create_user(db_session, SUNITA)
    household = (
        await db_session.execute(select(Household).where(Household.owner_user_id == sunita.id))
    ).scalar_one()

    await unlock.on_household_updated(
        db_session,
        OutboxEvent(
            event_type="identity.household_updated",
            payload={"household_id": str(household.id), "changed": ["address", "society_id"]},
        ),
    )

    assert (await _society(db_session, GREEN_VALLEY)).status == "live"


async def test_the_public_page_shows_unlocking_and_live_societies_only(
    db_session: AsyncSession,
) -> None:
    green = await community_api.public_society(db_session, "green-valley-heights-kharghar")
    assert green is not None and (green.status, green.joined, green.live_at) == (
        "unlocking",
        1,
        None,
    )
    assert await community_api.public_society(db_session, "panchavati-residency-kamothe") is None
    assert await community_api.public_society(db_session, "nowhere") is None
    society = await community_api.get_society(db_session, GREEN_VALLEY)
    assert society is not None and society.live_at is None


async def _all(db: AsyncSession) -> list[Society]:
    rows = await db.execute(
        select(Society).order_by(Society.id).execution_options(populate_existing=True)
    )
    return list(rows.scalars())
