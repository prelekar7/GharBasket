"""The messages for the events that arrive with their modules (spec §3.1): Kindness points, a
verified donation, a society going live, a published route (rider and captains, and again for a
new rider) and a kitchen's assignment offer."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User
from gharbasket.modules.notifications.jobs import on_event
from gharbasket.modules.notifications.models import Delivery, Message, Preference
from gharbasket.modules.notifications.render import format_value
from gharbasket.modules.notifications.service import inbox
from gharbasket.platform.locale import Language
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish, relay

from .conftest import MEENA, PRIYA, Home

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NOON = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)  # Mon 12:00 IST
LATE = datetime(2026, 10, 12, 18, 0, tzinfo=UTC)  # Mon 23:30 IST
SEVEN_AM = datetime(2026, 10, 13, 1, 30, tzinfo=UTC)  # Tue 07:00 IST
TUESDAY = "2026-10-13"


async def deliver(db: AsyncSession, event_type: str, payload: dict[str, Any]) -> list[Message]:
    """Hand one event to the consumer, as the relay would; the messages it made, by user."""
    event = OutboxEvent(id=uuid.uuid4(), event_type=event_type, payload=payload)
    await on_event(db, event)
    rows = await db.execute(select(Message).where(Message.event_id == event.id))
    return sorted(rows.scalars(), key=lambda m: str(m.user_id))


async def channels_of(db: AsyncSession, message: Message) -> dict[str, Delivery]:
    rows = await db.execute(select(Delivery).where(Delivery.message_id == message.id))
    return {d.channel: d for d in rows.scalars()}


async def shown(db: AsyncSession, user_id: uuid.UUID, lang: Language) -> list[tuple[str, str]]:
    """The user's inbox, newest first, as (title, body)."""
    items, _, _ = await inbox(db, user_id, lang, None, 50)
    return [(i.title, i.body) for i in items]


@pytest.fixture
async def third_user(db_session: AsyncSession, homes: dict[str, Home]) -> uuid.UUID:
    """Someone besides Priya and Meena: a rider, or a second captain."""
    taken = [home.user_id for home in homes.values()]
    found = await db_session.scalar(select(User.id).where(User.id.notin_(taken)).limit(1))
    assert found is not None
    return found


# --- Kindness -----------------------------------------------------------------------------------


@time_machine.travel(NOON, tick=False)
async def test_referral_points_notify_but_a_donations_points_leave_it_to_donation_verified(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA].user_id
    credited = {"user_id": str(priya), "points": 1_200, "expires_on": "2027-10-12"}
    credited |= {"source_id": str(uuid.uuid4())}

    assert (
        await deliver(
            db_session, "billing.points_credited", {**credited, "source_type": "donation"}
        )
        == []
    )
    (message,) = await deliver(
        db_session, "billing.points_credited", {**credited, "source_type": "referral"}
    )
    assert (message.user_id, message.kind, message.category, message.link, message.params) == (
        priya,
        "billing.points_credited",
        "kindness",
        "/kindness",
        {"points": 1_200},
    )
    (push,) = (await channels_of(db_session, message)).values()
    assert (push.channel, push.status) == ("push", "queued")
    assert await shown(db_session, priya, "en") == [
        (
            "You earned 1,200 Kindness points",
            "They are in your Kindness balance. Use them to donate a meal.",
        )
    ]


@time_machine.travel(NOON, tick=False)
async def test_a_verified_donation_tells_the_donor_her_points_and_follows_her_preference(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya, meena = homes[PRIYA], homes[MEENA]
    db_session.add(
        Preference(user_id=meena.user_id, category="kindness", push=False, whatsapp=False)
    )
    await db_session.flush()

    def donation(home: Home) -> dict[str, Any]:
        return {
            "donation_id": str(uuid.uuid4()),
            "user_id": str(home.user_id),
            "household_id": str(home.household_id),
            "hub_id": str(uuid.uuid4()),
            "society_id": str(uuid.uuid4()),
            "drive_id": None,
            "points": 100,
        }

    (hers,) = await deliver(db_session, "community.donation_verified", donation(priya))
    assert (hers.user_id, hers.kind, hers.category, hers.link, hers.params) == (
        priya.user_id,
        "community.donation_verified",
        "kindness",
        "/kindness",
        {"points": 100},
    )
    assert list(await channels_of(db_session, hers)) == ["push"]
    assert await shown(db_session, priya.user_id, "en") == [
        ("Your donation was verified", "Thank you. 100 Kindness points have been added.")
    ]

    # Meena turned Kindness pushes off: the inbox still gets it, in her language.
    (muted,) = await deliver(db_session, "community.donation_verified", donation(meena))
    assert await channels_of(db_session, muted) == {}
    assert await shown(db_session, meena.user_id, "hi") == [
        ("आपका दान सत्यापित हो गया", "धन्यवाद। 100 काइंडनेस पॉइंट जुड़ गए हैं।")
    ]


@time_machine.travel(NOON, tick=False)
async def test_a_society_going_live_tells_the_owner_of_every_household_in_it(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA].user_id
    household = await identity_api.household_for_user(db_session, priya)
    assert household is not None
    owners = await identity_api.society_owners(db_session, household.society_id)
    assert priya in owners

    payload = {
        "society_id": str(household.society_id),
        "hub_id": str(uuid.uuid4()),
        "joined": len(owners),
        "threshold": len(owners),
        "live_at": NOON.isoformat(),
    }
    messages = await deliver(db_session, "community.society_live", payload)
    assert {m.user_id for m in messages} == set(owners)
    for message in messages:
        assert (message.kind, message.category, message.link, message.params) == (
            "community.society_live",
            "kindness",
            f"/society/{household.society_id}",
            {},
        )
        assert list(await channels_of(db_session, message)) == ["push"]
    assert await shown(db_session, priya, "en") == [
        ("Your society is live", "Delivery is now free for everyone here.")
    ]
    # A society with no households has no one to tell.
    assert (
        await deliver(
            db_session, "community.society_live", {**payload, "society_id": str(uuid.uuid4())}
        )
        == []
    )


# --- Partner work -------------------------------------------------------------------------------


def stop(
    seq: int,
    society: uuid.UUID | None,
    captain: uuid.UUID | None,
    bags: int,
    eta: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": "drop",
        "society_id": None if society is None else str(society),
        "slot_id": str(uuid.uuid4()),
        "eta": eta,
        "bags": bags,
        "captain_user_id": None if captain is None else str(captain),
        "partner_id": None,
    } | extra


def route(
    rider: uuid.UUID | None,
    stops: list[dict[str, Any]],
    *,
    kind: str = "box",
    previous: uuid.UUID | None = None,
    route_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    return {
        "route_id": str(route_id or uuid.uuid4()),
        "plan_id": str(uuid.uuid4()),
        "hub_id": str(uuid.uuid4()),
        "delivery_date": TUESDAY,
        "kind": kind,
        "trip": 1,
        "vehicle_id": str(uuid.uuid4()),
        "rider_user_id": None if rider is None else str(rider),
        "previous_rider_user_id": None if previous is None else str(previous),
        "depart": "06:30",
        "stops": stops,
    }


@time_machine.travel(NOON, tick=False)
async def test_a_published_route_tells_the_rider_and_each_captain_in_their_own_words(
    db_session: AsyncSession, homes: dict[str, Home], third_user: uuid.UUID
) -> None:
    priya, meena, rider = homes[PRIYA].user_id, homes[MEENA].user_id, third_user
    one, two = uuid.uuid4(), uuid.uuid4()
    stops = [
        stop(1, one, priya, 8, "07:20"),
        stop(2, one, priya, 6, "07:50"),  # a second slot at the same society: bags add up
        stop(3, two, meena, 5, "08:30"),
        stop(4, uuid.uuid4(), None, 3, "09:00"),  # a society without a captain
    ]
    by_user = {
        m.user_id: m
        for m in await deliver(db_session, "logistics.route_published", route(rider, stops))
    }
    assert set(by_user) == {rider, priya, meena}

    as_rider = by_user[rider]
    assert (as_rider.kind, as_rider.category, as_rider.link, as_rider.params) == (
        "logistics.route_published.rider",
        "account",
        "/rider",
        {"day": TUESDAY, "stops": 4, "depart": "06:30"},
    )
    as_captain = by_user[priya]
    assert (as_captain.kind, as_captain.category, as_captain.link, as_captain.params) == (
        "logistics.route_published.captain",
        "account",
        "/captain",
        {"day": TUESDAY, "bags": 14, "eta": "07:20"},
    )
    assert by_user[meena].params == {"day": TUESDAY, "bags": 5, "eta": "08:30"}
    for message in by_user.values():  # account messages are always pushed
        assert list(await channels_of(db_session, message)) == ["push"]

    assert await shown(db_session, rider, "en") == [
        ("Your Tuesday route is ready", "4 stops. Leave at 6:30 AM.")
    ]
    assert await shown(db_session, priya, "en") == [
        ("Your society's bags on Tuesday", "14 in all. The van is expected at about 7:20 AM.")
    ]
    assert await shown(db_session, meena, "hi") == [
        ("मंगलवार को आपकी सोसाइटी के बैग", "कुल 5। वैन लगभग सुबह 8:30 बजे पहुँचेगी।")
    ]
    assert await shown(db_session, rider, "mr") == [
        ("मंगळवार चा तुमचा रूट तयार आहे", "4 थांबे. सकाळी 6:30 वाजता निघा.")
    ]


@time_machine.travel(NOON, tick=False)
async def test_a_tiffin_run_reads_as_a_lunch_run(
    db_session: AsyncSession, homes: dict[str, Home], third_user: uuid.UUID
) -> None:
    priya, rider = homes[PRIYA].user_id, third_user
    kitchen = {"kind": "kitchen"}
    society = uuid.uuid4()
    stops = [
        stop(1, None, None, 4, "11:30", **kitchen, partner_id=str(uuid.uuid4())),
        stop(2, None, None, 2, "11:50", **kitchen, partner_id=str(uuid.uuid4())),
        stop(3, society, priya, 4, "12:50"),
        stop(4, uuid.uuid4(), None, 2, "13:10"),
    ]
    messages = await deliver(
        db_session, "logistics.route_published", route(rider, stops, kind="tiffin")
    )
    by_user = {m.user_id: m for m in messages}
    assert (by_user[rider].kind, by_user[rider].params) == (
        "logistics.route_published.rider_tiffin",
        {"day": TUESDAY, "kitchens": 2, "societies": 2, "first_pickup": "11:30"},
    )
    assert (by_user[priya].kind, by_user[priya].params) == (
        "logistics.route_published.captain_tiffin",
        {"day": TUESDAY, "bags": 4, "eta": "12:50"},
    )
    assert await shown(db_session, rider, "en") == [
        ("Your tiffin run on Tuesday", "2 kitchens, 2 societies. First pickup at 11:30 AM.")
    ]
    assert await shown(db_session, priya, "en") == [
        ("Your society's tiffins on Tuesday", "4 in all. The rider is expected at about 12:50 PM.")
    ]


@time_machine.travel(NOON, tick=False)
async def test_a_new_rider_gets_the_route_again_and_the_captains_are_not_told_twice(
    db_session: AsyncSession, homes: dict[str, Home], third_user: uuid.UUID
) -> None:
    priya, meena = homes[PRIYA].user_id, homes[MEENA].user_id
    stops = [stop(1, uuid.uuid4(), priya, 8, "07:20")]
    route_id = uuid.uuid4()

    # No rider yet: the captain is told, nobody else.
    first = await deliver(
        db_session, "logistics.route_published", route(None, stops, route_id=route_id)
    )
    assert [(m.user_id, m.kind) for m in first] == [(priya, "logistics.route_published.captain")]

    # A hub manager gives the route its first rider (nothing in the event says it is a re-send):
    # the rider is told, and the captain's identical message is not repeated.
    again = await deliver(
        db_session, "logistics.route_published", route(meena, stops, route_id=route_id)
    )
    assert [(m.user_id, m.kind) for m in again] == [(meena, "logistics.route_published.rider")]

    # A rider change: the new rider is told, the captains and the old rider are not.
    changed = await deliver(
        db_session,
        "logistics.route_published",
        route(third_user, stops, previous=meena, route_id=route_id),
    )
    assert [(m.user_id, m.kind) for m in changed] == [
        (third_user, "logistics.route_published.rider")
    ]
    assert changed[0].params == {"day": TUESDAY, "stops": 1, "depart": "06:30"}
    assert [
        m.kind
        for m in (
            await db_session.execute(select(Message).where(Message.user_id == meena))
        ).scalars()
    ] == ["logistics.route_published.rider"]

    # The rider taken off a route leaves no one to tell.
    unassigned = route(None, stops, previous=third_user, route_id=route_id)
    assert await deliver(db_session, "logistics.route_published", unassigned) == []

    # A captain whose bags changed since is told again.
    more = [stop(1, uuid.uuid4(), priya, 9, "07:20")]
    changed_bags = await deliver(db_session, "logistics.route_published", route(None, more))
    assert [m.params["bags"] for m in changed_bags] == [9]


@time_machine.travel(NOON, tick=False)
async def test_someone_who_is_both_rider_and_captain_gets_one_message(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA].user_id
    stops = [stop(1, uuid.uuid4(), priya, 8, "07:20")]
    messages = await deliver(db_session, "logistics.route_published", route(priya, stops))
    assert [m.kind for m in messages] == ["logistics.route_published.rider"]


def offer(user_id: uuid.UUID, meal: str = "lunch") -> dict[str, Any]:
    return {
        "assignment_id": str(uuid.uuid4()),
        "partner_id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "hub_id": str(uuid.uuid4()),
        "date": TUESDAY,
        "meal": meal,
        "portions": 12,
        "orders_count": 5,
        "respond_by": "2026-10-12T17:00:00Z",  # 22:30 IST
        "round": 1,
    }


@time_machine.travel(NOON, tick=False)
async def test_an_assignment_offer_tells_the_kitchen_what_is_asked_and_by_when(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    kitchen = homes[PRIYA].user_id
    (message,) = await deliver(db_session, "partners.assignment_created", offer(kitchen))
    assert (message.user_id, message.kind, message.category, message.link, message.params) == (
        kitchen,
        "partners.assignment_created",
        "account",
        "/kitchen",
        {"portions": 12, "meal": "lunch", "day": TUESDAY, "respond_by": "2026-10-12T17:00:00Z"},
    )
    assert list(await channels_of(db_session, message)) == ["push"]
    assert await shown(db_session, kitchen, "en") == [
        ("New tiffin order for Tuesday", "12 portions for lunch. Reply before 10:30 PM.")
    ]
    (dinner,) = await deliver(db_session, "partners.assignment_created", offer(kitchen, "dinner"))
    assert dinner.params["meal"] == "dinner"
    assert (await shown(db_session, kitchen, "hi"))[0] == (
        "मंगलवार के लिए नया टिफ़िन ऑर्डर",
        "रात का खाना: 12 पोर्शन। रात 10:30 बजे से पहले जवाब दें।",
    )


async def test_an_offer_skips_quiet_hours_but_a_route_waits_for_morning(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    """An offer lapses at 22:30, so a held one would arrive after it did; the route can wait."""
    kitchen = homes[PRIYA].user_id
    with time_machine.travel(LATE, tick=False):
        (offered,) = await deliver(db_session, "partners.assignment_created", offer(kitchen))
        (routed,) = await deliver(
            db_session,
            "logistics.route_published",
            route(kitchen, [stop(1, uuid.uuid4(), None, 8, "07:20")]),
        )
    now = (await channels_of(db_session, offered))["push"]
    assert (now.status, now.next_attempt_at) == ("queued", LATE)
    held = (await channels_of(db_session, routed))["push"]
    assert (held.status, held.next_attempt_at) == ("held", SEVEN_AM)


@pytest.mark.parametrize(
    ("event_type", "payload", "error"),
    [
        ("billing.points_credited", {"user_id": "{user}", "points": 5}, "KeyError"),  # no source
        (
            "billing.points_credited",
            {"user_id": "{user}", "points": "many", "source_type": "referral"},
            "ValueError",
        ),
        ("community.donation_verified", {"user_id": "{user}", "points": None}, "TypeError"),
        ("community.society_live", {"society_id": "nowhere"}, "ValueError"),
        (
            "partners.assignment_created",
            {**offer(uuid.uuid4(), "brunch"), "user_id": "{user}"},
            "KeyError",
        ),
        ("partners.assignment_created", {"user_id": "{user}", "date": TUESDAY}, "KeyError"),
        ("logistics.route_published", route(None, []) | {"kind": "van"}, "KeyError"),
        (
            "logistics.route_published",
            route(None, [stop(1, uuid.uuid4(), uuid.uuid4(), 1, "7am")]),
            "ValueError",
        ),
    ],
)
async def test_a_bad_payload_is_logged_and_skipped_without_raising(
    db_session: AsyncSession,
    homes: dict[str, Home],
    event_type: str,
    payload: dict[str, Any],
    error: str,
) -> None:
    user = str(homes[PRIYA].user_id)
    payload = {k: user if v == "{user}" else v for k, v in payload.items()}
    event = OutboxEvent(id=uuid.uuid4(), event_type=event_type, payload=payload)
    with structlog.testing.capture_logs() as logs:
        await on_event(db_session, event)  # must not raise: other consumers share the savepoint
    assert (await db_session.execute(select(Message))).scalars().all() == []
    (dropped,) = [e for e in logs if e["event"] == "notification_event_unreadable"]
    assert (dropped["event_type"], dropped["error"]) == (event_type, error)


@time_machine.travel(NOON, tick=False)
async def test_the_relay_delivers_each_event_once_even_when_it_is_handed_over_again(
    db_session: AsyncSession, homes: dict[str, Home]
) -> None:
    priya = homes[PRIYA].user_id
    event_id = await publish(db_session, "partners.assignment_created", offer(priya))
    assert (await relay(db_session)).failed == 0
    event = await db_session.get(OutboxEvent, event_id)
    assert event is not None
    await on_event(db_session, event)
    messages = (await db_session.execute(select(Message).where(Message.user_id == priya))).scalars()
    assert [m.event_type for m in messages] == ["partners.assignment_created"]


def test_the_new_placeholder_types_read_in_each_language() -> None:
    assert format_value("number", 1_234_567, "en") == "12,34,567"
    assert format_value("number", "12", "hi") == "12"
    assert [format_value("clock", "07:20", lang) for lang in ("en", "hi", "mr")] == [
        "7:20 AM",
        "सुबह 7:20 बजे",
        "सकाळी 7:20",
    ]
    assert format_value("clock", "13:00", "en") == "1 PM"
    assert [format_value("meal", "dinner", lang) for lang in ("en", "hi", "mr")] == [
        "dinner",
        "रात का खाना",
        "रात्रीचे जेवण",
    ]
    for bad in ("7am", "25:00", "07:20:00"):
        with pytest.raises(ValueError):
            format_value("clock", bad, "en")
    with pytest.raises(KeyError):
        format_value("meal", "brunch", "en")
