"""Know your cook and ratings (M8 §3.5): the kitchen's public card, a household rating a delivered
tiffin once within 48 hours, the kitchen's rating from her last 50, and the 14:30 prompt."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import kitchens, ratings
from gharbasket.modules.partners.models import Partner, Rating
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import (
    SEED_HUB_ID,
    seed,
    seed_households,
    seed_hubs,
    seed_partners,
    seed_societies,
)

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..seed_cache import seed_catalog
from .conftest import LATA, MEENA, PRIYA, SUNITA, VIKRAM, as_user
from .test_cooking import CHECKLIST, ON_THE_DAY
from .tiffins import EVENING, T, kitchen, kitchen_row, tag, tiffin_order, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("tiffin_world")]

RUN = f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run"
PROMPT = "/v1/internal/jobs/prompt_tiffin_ratings"


@pytest.fixture
async def tiffin_world(db_session: AsyncSession) -> None:
    await seed(db_session)
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await seed_partners(db_session)
    await tag(db_session, "tomato", "veg")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    at = [EVENING]
    monkeypatch.setattr(kitchens, "now", lambda: at[0])
    return at


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def cooked(
    api: httpx2.AsyncClient,
    google: FakeGoogle,
    db: AsyncSession,
    clock: list[datetime],
    orders: list[uuid.UUID],
    *,
    deliver: bool = True,
) -> tuple[uuid.UUID, dict[str, str]]:
    """Lata's kitchen takes the orders through accept, ready, pickup and (by default) delivery;
    the events reach M8's consumers. Returns her partner id and her auth header."""
    vikram, _ = await as_user(api, google, VIKRAM)
    await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})
    lata_auth, _ = await as_user(api, google, LATA)
    (offer,) = (await api.get(f"/v1/assignments?date={T}", headers=lata_auth)).json()["items"]
    assert (
        await api.post(f"/v1/assignments/{offer['id']}/accept", headers={**lata_auth, **key()})
    ).status_code == 200
    clock[0] = ON_THE_DAY
    ready = await api.post(
        f"/v1/assignments/{offer['id']}/ready",
        json={"checklist": CHECKLIST},
        headers={**lata_auth, **key()},
    )
    assert ready.status_code == 200, ready.text
    if deliver:
        for order in orders:
            await orders_api.transition(
                db, order, "dispatched", actor="rider", actor_user_id=uuid.uuid4()
            )
            await orders_api.transition(
                db, order, "delivered", actor="rider", actor_user_id=uuid.uuid4()
            )
        await db.commit()
        while (await relay(db)).sent:
            pass
    return uuid.UUID(offer["partner"]["id"]), lata_auth


async def test_a_household_rates_its_delivered_tiffin_once_and_only_its_owner_may(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    await kitchen(db_session, LATA, "Lata tai", lunch=5)
    order = await tiffin_order(db_session, PRIYA, 2)
    lata, _ = await cooked(api, google, db_session, clock, [order], deliver=False)
    priya, _ = await as_user(api, google, PRIYA)
    sunita, _ = await as_user(api, google, SUNITA)
    body = {"order_id": str(order), "score": 4, "comment": "  Tasty,   a bit late "}

    early = await api.post("/v1/ratings", json=body, headers={**priya, **key()})
    assert code(early) == (409, "partners.rating_window_closed")  # packed, not delivered yet
    await orders_api.transition(
        db_session, order, "dispatched", actor="rider", actor_user_id=uuid.uuid4()
    )
    await orders_api.transition(
        db_session, order, "delivered", actor="rider", actor_user_id=uuid.uuid4()
    )
    await db_session.commit()
    clock[0] = datetime.now(UTC)  # M4 stamps the delivery with the database's clock

    stranger = await api.post("/v1/ratings", json=body, headers={**sunita, **key()})
    bad_score = await api.post("/v1/ratings", json=body | {"score": 6}, headers={**priya, **key()})
    first_key = {**priya, **key()}
    done = await api.post("/v1/ratings", json=body, headers=first_key)
    replay = await api.post("/v1/ratings", json=body, headers=first_key)
    twice = await api.post("/v1/ratings", json=body | {"score": 1}, headers={**priya, **key()})
    no_key = await api.post("/v1/ratings", json=body, headers=priya)

    assert code(stranger) == (404, "partners.rating_not_found")
    assert bad_score.status_code == 422
    assert done.status_code == 201, done.text
    assert done.json()["score"] == 4 and done.json()["partner_id"] == str(lata)
    assert done.json()["comment"] == "Tasty, a bit late"
    assert replay.headers.get("Idempotent-Replayed") == "true" and replay.json() == done.json()
    assert code(twice) == (409, "partners.rating_exists")
    assert no_key.status_code == 400

    mine = await api.get(f"/v1/ratings?order_id={order}", headers=priya)
    other = await api.get(f"/v1/ratings?order_id={order}", headers=sunita)
    unknown = await api.get(f"/v1/ratings?order_id={uuid.uuid4()}", headers=priya)
    assert mine.status_code == 200 and mine.json() == done.json()
    assert mine.headers["Cache-Control"] == "no-store"
    assert code(other) == (404, "partners.rating_not_found") == code(unknown)


async def test_the_window_closes_48_hours_after_delivery_and_a_missed_kitchen_has_no_rating(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    await kitchen(db_session, LATA, "Lata tai", lunch=5)
    order = await tiffin_order(db_session, PRIYA, 2)
    orphan = await tiffin_order(db_session, PRIYA, 2, slug="tomato", start=EVENING.time())
    await cooked(api, google, db_session, clock, [order])
    priya, _ = await as_user(api, google, PRIYA)

    clock[0] = datetime.now(UTC) + timedelta(hours=49)
    late = await api.post(
        "/v1/ratings", json={"order_id": str(order), "score": 5}, headers={**priya, **key()}
    )
    assert code(late) == (409, "partners.rating_window_closed")
    # An order no kitchen cooked (no assignment holds it) cannot be rated.
    clock[0] = datetime.now(UTC)
    nobody = await api.post(
        "/v1/ratings", json={"order_id": str(orphan), "score": 5}, headers={**priya, **key()}
    )
    assert code(nobody) == (404, "partners.rating_not_found")
    box = await api.post(
        "/v1/ratings", json={"order_id": str(uuid.uuid4()), "score": 5}, headers={**priya, **key()}
    )
    assert code(box) == (404, "partners.rating_not_found")


async def test_the_kitchens_rating_is_the_mean_of_her_last_50_and_shows_from_five(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    lata_id = await kitchen(db_session, LATA, "Lata tai", lunch=10)
    orders = [await tiffin_order(db_session, PRIYA, 1) for _ in range(5)]
    await cooked(api, google, db_session, clock, orders)
    priya, _ = await as_user(api, google, PRIYA)
    clock[0] = datetime.now(UTC)

    scores = [5, 4, 4, 3, 5]
    for n, (order, score) in enumerate(zip(orders, scores, strict=True), start=1):
        done = await api.post(
            "/v1/ratings", json={"order_id": str(order), "score": score}, headers={**priya, **key()}
        )
        assert done.status_code == 201, done.text
        card = (await api.get(f"/v1/partners/{lata_id}/card", headers=priya)).json()
        assert card["rating"] == (None if n < 5 else {"average": 4.2, "count": 5})
    partner = await db_session.get(Partner, lata_id, populate_existing=True)
    assert partner is not None and (float(partner.rating_avg or 0), partner.rating_count) == (
        4.2,
        5,
    )
    # Ratings change the aggregate, not the record's ETag: a pending rename still matches.
    assert partner.version == 1

    # Fifty-two ratings: only the latest fifty count, and the count is all of them. The real five
    # are given earlier dates (the shared test transaction stamps them all alike).
    first = datetime(2026, 1, 1, tzinfo=UTC)
    for n, rating in enumerate(
        await ratings.given_by(db_session, await user_id(db_session, PRIYA))
    ):
        rating.created_at = first + timedelta(minutes=n)
    base = datetime(2027, 1, 1, tzinfo=UTC)
    for n in range(47):
        db_session.add(
            Rating(
                order_id=uuid.uuid4(),
                partner_id=lata_id,
                household_id=uuid.uuid4(),
                user_id=uuid.uuid4(),
                score=1 if n < 2 else 5,
                created_at=base + timedelta(minutes=n),
            )
        )
    await db_session.flush()
    await ratings.refresh(db_session, partner)
    assert partner.rating_count == 52
    # The latest fifty: the 47 of 2027 (45 fives, 2 ones) and the real three newest (4, 3, 5).
    assert float(partner.rating_avg or 0) == pytest.approx((45 * 5 + 2 * 1 + 4 + 3 + 5) / 50)


async def test_a_kitchen_under_the_rating_floor_is_not_offered_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    lata_id = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    partner = await db_session.get(Partner, lata_id)
    assert partner is not None
    partner.rating_avg, partner.rating_count = Decimal("3.00"), 6  # below the floor of 3.8
    await db_session.commit()
    await tiffin_order(db_session, PRIYA, 2)

    run = await api.post(RUN, json={"date": T.isoformat()}, headers={**vikram, **key()})

    assert (run.json()["offers"], run.json()["unassigned"]) == (0, 1)
    unassigned = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "partners.tiffins_unassigned")
    )
    assert unassigned.scalar_one()["reasons"] == ["below_rating_floor"]


async def test_the_card_shows_the_kitchen_and_never_where_she_lives(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    lata_id = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    row = await kitchen_row(db_session, lata_id)
    row.area, row.address = "Sector 20, Kharghar", "B-1103, Sunrise Towers"
    row.fssai_reg_no, row.hygiene_grade = "11521999000123", "A"
    row.inspected_on = datetime(2026, 12, 20, tzinfo=UTC).date()
    await db_session.commit()
    order = await tiffin_order(db_session, PRIYA, 3)
    await cooked(api, google, db_session, clock, [order])
    priya, _ = await as_user(api, google, PRIYA)
    meena, meena_user = await as_user(api, google, MEENA)

    card = await api.get(f"/v1/partners/{lata_id}/card", headers=priya)

    assert card.status_code == 200, card.text
    assert card.headers["Cache-Control"] == "no-store"
    body = card.json()
    assert body["display_name"] == "Lata tai" and body["area"] == "Sector 20, Kharghar"
    assert body["fssai_reg_no"] == "11521999000123" and body["skills"] == ["veg"]
    assert body["hygiene"] == {"grade": "A", "inspected_on": "2026-12-20"}
    assert body["rating"] is None and body["photo_url"] is None
    assert body["portions_served"] == 3 and len(body["since"]) == 7
    assert set(body) == {
        "partner_id",
        "display_name",
        "photo_url",
        "area",
        "since",
        "fssai_reg_no",
        "fssai_valid_until",
        "hygiene",
        "rating",
        "skills",
        "portions_served",
    }
    assert "B-1103" not in str(body) and "+91" not in str(body)
    # Not a kitchen (a captain), a paused kitchen is shown, a suspended or unknown one is not.
    captain = await partners_api.partner_for_user(db_session, meena_user, "captain")
    assert captain is not None
    assert (
        code(await api.get(f"/v1/partners/{captain.id}/card", headers=priya))[1]
        == "partners.partner_not_found"
    )
    assert (await api.get(f"/v1/partners/{uuid.uuid4()}/card", headers=meena)).status_code == 404
    assert (await api.get(f"/v1/partners/{lata_id}/card")).status_code == 401
    partner = await db_session.get(Partner, lata_id)
    assert partner is not None
    partner.status = "paused"
    await db_session.commit()
    assert (await api.get(f"/v1/partners/{lata_id}/card", headers=priya)).status_code == 200
    partner.status = "suspended"
    partner.suspended_at, partner.suspend_reason_code = datetime.now(UTC), "hygiene"
    await db_session.commit()
    assert (await api.get(f"/v1/partners/{lata_id}/card", headers=priya)).status_code == 404


async def test_the_1430_job_prompts_each_delivered_lunch_once_and_skips_rated_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    await kitchen(db_session, LATA, "Lata tai", lunch=5)
    rated = await tiffin_order(db_session, PRIYA, 1)
    waiting = await tiffin_order(db_session, MEENA, 1)
    lata, _ = await cooked(api, google, db_session, clock, [rated, waiting])
    priya, priya_id = await as_user(api, google, PRIYA)
    clock[0] = datetime.now(UTC)
    assert (
        await api.post(
            "/v1/ratings", json={"order_id": str(rated), "score": 5}, headers={**priya, **key()}
        )
    ).status_code == 201
    scheduled = {"scheduled_for": "2027-01-15T09:00:00Z"}  # T 14:30 IST

    first = await api.post(PROMPT, json=scheduled)
    again = await api.post(PROMPT, json=scheduled)
    other_day = await api.post(PROMPT, json={"scheduled_for": "2027-01-16T09:00:00Z"})

    assert first.json()["summary"] == {"delivery_date": T.isoformat(), "prompted": 1}
    assert again.json()["summary"]["prompted"] == 0 == other_day.json()["summary"]["prompted"]
    asked = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "partners.rating_requested")
    )
    household = await orders_api.get(db_session, waiting)
    assert asked.scalars().all() == [
        {
            "order_id": str(waiting),
            "household_id": str(household.household_id),
            "partner_id": str(lata),
        }
    ]
    exported = await partners_api.export_for_user(db_session, priya_id)
    (given,) = exported["ratings"]
    assert given["order_id"] == rated and "user_id" not in given
    assert await user_id(db_session, PRIYA) == priya_id


async def test_the_purge_erases_who_rated_and_what_they_wrote_but_keeps_the_score(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, clock: list[datetime]
) -> None:
    api = clients()
    lata_id = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    mine = await tiffin_order(db_session, PRIYA, 1)
    theirs = await tiffin_order(db_session, MEENA, 1)
    await cooked(api, google, db_session, clock, [mine, theirs])
    priya, priya_id = await as_user(api, google, PRIYA)
    meena, meena_id = await as_user(api, google, MEENA)
    clock[0] = datetime.now(UTC)
    for headers, order, score in ((priya, mine, 2), (meena, theirs, 4)):
        body = {"order_id": str(order), "score": score, "comment": "private words"}
        answer = await api.post("/v1/ratings", json=body, headers={**headers, **key()})
        assert answer.status_code == 201, answer.text

    erased = await partners_api.anonymise_for_user(db_session, priya_id, None)
    again = await partners_api.anonymise_for_user(db_session, priya_id, None)

    assert (erased, again) == ({"ratings": 1}, {"ratings": 0})
    rows = {r.order_id: r for r in (await db_session.execute(select(Rating))).scalars()}
    for row in rows.values():
        await db_session.refresh(row)
    assert (rows[mine].user_id, rows[mine].comment, rows[mine].score) == (None, None, 2)
    assert (rows[theirs].user_id, rows[theirs].comment) == (meena_id, "private words")
    assert (await partners_api.export_for_user(db_session, priya_id))["ratings"] == []
    partner = await db_session.get(Partner, lata_id, populate_existing=True)
    assert partner is not None and partner.rating_count == 2  # the mean is untouched
