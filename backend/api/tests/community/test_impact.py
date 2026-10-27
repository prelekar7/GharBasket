"""Customers' impact (M9 §3.7): a household's own numbers sum its verified donations, converted
referrals and donated meals; a society's are for its residents, captain, hub staff and admins, and
every number is `null` until 5 households contributed (k = 5), also through the facade that M12's
public page reads."""

import copy
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import impact
from gharbasket.modules.community.models import (
    Donation,
    MealPledge,
    Ngo,
    NgoHandoff,
    Referral,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.households import create_household
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.identity.schemas import HouseholdIn
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.errors import ProblemError
from gharbasket.tools.seed import HOUSEHOLDS, SEED_HUB_ID, SEED_NGO_ID, SEED_SOCIETY_ID, SOCIETIES

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("captain_world")]

IST = ZoneInfo("Asia/Kolkata")
PRIYA = "+919800001001"  # Sunrise Towers
SUNITA = "+919800001002"  # Green Valley Heights
MEENA = "+919800001004"  # Sunrise Towers, and its captain
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
ASHA = "+919800001010"  # ngo_coordinator
NEHA = "+919800001011"  # admin
GREEN_VALLEY = next(s.id for s in SOCIETIES if s.slug == "green-valley-heights-kharghar")
OTHER_HUB = uuid.UUID("0199a000-0000-7000-8000-00000000a0ff")
PERSONAL = ("household", "flat", "A-1203", "tower", "Priya", "Sunita", "Meena", "+91980000")


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _with_role(
    api: Any, google: FakeGoogle, db: AsyncSession, phone: str, role: str, scope: uuid.UUID
) -> dict[str, str]:
    """Headers of a new person holding only `role:<scope>` (X-Active-Role)."""
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    user = await db.get(User, uuid.UUID(signed["user"]["id"]))
    assert user is not None
    db.add(UserRole(user_id=user.id, role=role, scope_type="hub", scope_id=scope))
    await db.commit()
    refreshed = await api.post("/v1/auth/refresh", json={"refresh_token": signed["refresh_token"]})
    return {**bearer(refreshed.json()["access_token"]), "X-Active-Role": f"{role}:{scope}"}


async def move_in(
    db: AsyncSession, n: int, society_id: uuid.UUID = GREEN_VALLEY
) -> tuple[uuid.UUID, uuid.UUID]:
    """A new household in the society (Sunita's onboarding with another flat): (user, household)."""
    user, _ = await find_or_create_user(db, f"+9198000066{n:02d}")
    body = copy.deepcopy(HOUSEHOLDS[SUNITA])
    body["society_id"] = str(society_id)
    body["address"] = {"tower_id": "b", "flat": f"B-{n}", "location": None}
    household = await create_household(db, user.id, HouseholdIn.model_validate(body))
    return user.id, household.id


async def household_of(db: AsyncSession, phone: str) -> tuple[uuid.UUID, uuid.UUID]:
    user, _ = await find_or_create_user(db, phone)
    household = await identity_api.household_for_user(db, user.id)
    assert household is not None
    return user.id, household.id


def line(code: str, unit: str, counted: float) -> dict[str, Any]:
    return {"code": code, "unit": unit, "qty": counted, "counted": counted, "rate": 10, "points": 0}


async def donate(
    db: AsyncSession,
    who: tuple[uuid.UUID, uuid.UUID],
    items: list[dict[str, Any]],
    *,
    society_id: uuid.UUID = SEED_SOCIETY_ID,
    status: str = "verified",
    points: int | None = 100,
    verified_at: datetime | None = None,
) -> Donation:
    donation = Donation(
        user_id=who[0],
        household_id=who[1],
        hub_id=SEED_HUB_ID,
        society_id=society_id,
        items=items,
        status=status,
        pickup_on=date(2026, 10, 13),
        estimated_points=150,
        points_awarded=points if status in ("verified", "handed_over") else None,
        verified_at=(verified_at or datetime.now(UTC))
        if status in ("verified", "handed_over")
        else None,
    )
    db.add(donation)
    await db.flush()
    return donation


async def pledge(
    db: AsyncSession, user_id: uuid.UUID, meals: int, at: datetime | None = None
) -> None:
    db.add(
        MealPledge(
            entry_id=uuid.uuid4(),
            user_id=user_id,
            hub_id=SEED_HUB_ID,
            meals=meals,
            pledged_at=at or datetime.now(UTC),
        )
    )
    await db.flush()


async def convert(
    db: AsyncSession,
    referrer: uuid.UUID,
    referee: tuple[uuid.UUID, uuid.UUID],
    society_id: uuid.UUID,
    *,
    points: tuple[int, int] = (250, 250),
) -> None:
    db.add(
        Referral(
            code="ABC234",
            referrer_user_id=referrer,
            referee_user_id=referee[0],
            referee_household_id=referee[1],
            society_id=society_id,
            hub_id=SEED_HUB_ID,
            status="converted",
            joined_on=date.today(),
            converted_on=date.today(),
            referrer_points=points[0],
            referee_points=points[1],
        )
    )
    await db.flush()


def keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for inner in value.values() for k in keys(inner)}
    if isinstance(value, list):
        return {k for inner in value for k in keys(inner)}
    return set()


# --- me -------------------------------------------------------------------------------------------


async def test_my_impact_sums_verified_donations_referrals_and_meals(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    priya = await household_of(db_session, PRIYA)
    sunita = await household_of(db_session, SUNITA)
    # 2.5 kg of clothes and 2 toys: 4.5 pieces counted, half up 5; 2.5 kg
    await donate(
        db_session, priya, [line("clothes", "kg", 2.5), line("toys", "each", 2)], points=130
    )
    # handed over counts too; 1.5 kg and 1 book: 2.5 pieces, half up 3
    await donate(
        db_session,
        priya,
        [line("clothes", "kg", 1.5), line("books", "each", 1)],
        status="handed_over",
        points=60,
    )
    # Not yet counted, rejected, someone else's: none of them are hers
    await donate(db_session, priya, [line("clothes", "kg", 9)], status="scheduled")
    await donate(db_session, priya, [line("clothes", "kg", 9)], status="rejected")
    await donate(db_session, sunita, [line("clothes", "kg", 7)], points=999)
    # She invited one neighbour, who earned her 250 points
    await convert(db_session, priya[0], (uuid.uuid4(), uuid.uuid4()), SEED_SOCIETY_ID)
    await pledge(db_session, priya[0], 2)
    await pledge(db_session, sunita[0], 5)
    api = clients()
    auth = await _as(api, google, PRIYA)

    answer = await api.get("/v1/impact", params={"scope": "me"}, headers=auth)

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "no-store"
    assert answer.json() == {
        "scope": "me",
        "period": "all",
        "items_donated": 8,
        "kg_donated": 4.0,
        "donations_verified": 2,
        "points_earned": 130 + 60 + 250,
        "meals_donated": 2,
        "homes_invited": 1,
        "donated": [
            {"code": "books", "unit": "each", "qty": 1.0},
            {"code": "clothes", "unit": "kg", "qty": 4.0},
            {"code": "toys", "unit": "each", "qty": 2.0},
        ],
    }


async def test_a_referred_neighbour_earns_her_welcome_points_and_a_stranger_sees_zeros(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    priya = await household_of(db_session, PRIYA)
    sunita = await household_of(db_session, SUNITA)
    await convert(db_session, priya[0], sunita, GREEN_VALLEY, points=(250, 100))
    api = clients()

    sunita_auth = await _as(api, google, SUNITA)
    sunitas = (await api.get("/v1/impact", params={"scope": "me"}, headers=sunita_auth)).json()
    assert (sunitas["points_earned"], sunitas["homes_invited"]) == (100, 0)  # the referee's points
    meena_auth = await _as(api, google, MEENA)
    nothing = (await api.get("/v1/impact", params={"scope": "me"}, headers=meena_auth)).json()
    assert nothing["donated"] == [] and nothing["items_donated"] == nothing["points_earned"] == 0


async def test_my_impact_is_for_customers_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await _with_role(api, google, db_session, "+919800009811", "hub_manager", SEED_HUB_ID)
    refused = await api.get("/v1/impact", params={"scope": "me"}, headers=manager)
    assert refused.status_code == 403 and refused.json()["code"] == "auth.forbidden"
    anonymous = await api.get("/v1/impact", params={"scope": "me"})
    assert anonymous.status_code == 401
    missing = await api.get("/v1/impact", headers=await _as(api, google, PRIYA))
    assert missing.status_code == 422  # a scope is required


# --- a society: who may read ----------------------------------------------------------------------


async def test_a_society_is_for_residents_its_captain_its_hub_staff_and_admins(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    params = {"scope": "society", "society_id": str(SEED_SOCIETY_ID)}

    for phone in (PRIYA, MEENA, VIKRAM, SANJAY, NEHA):  # resident, resident-captain, staff, admin
        answer = await api.get("/v1/impact", params=params, headers=await _as(api, google, phone))
        assert answer.status_code == 200, (phone, answer.text)
    assert answer.headers["Cache-Control"] == "private, max-age=60"

    meena = await _as(api, google, MEENA)
    as_captain = await api.get(
        "/v1/impact",
        params=params,
        headers={**meena, "X-Active-Role": f"captain:{SEED_SOCIETY_ID}"},
    )
    assert as_captain.status_code == 200, as_captain.text  # her role alone is enough

    for phone in (SUNITA, ASHA):  # a resident of another society; an NGO coordinator
        refused = await api.get("/v1/impact", params=params, headers=await _as(api, google, phone))
        assert refused.status_code == 403 and refused.json()["code"] == "auth.forbidden", phone
    elsewhere = await _with_role(api, google, db_session, "+919800009812", "hub_manager", OTHER_HUB)
    other_hub = await api.get("/v1/impact", params=params, headers=elsewhere)
    assert other_hub.status_code == 403
    assert (await api.get("/v1/impact", params=params)).status_code == 401


async def test_the_society_and_period_are_checked(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await _as(api, google, NEHA)
    params = {"scope": "society", "society_id": str(SEED_SOCIETY_ID)}

    unknown = await api.get(
        "/v1/impact", params={**params, "society_id": str(uuid.uuid4())}, headers=admin
    )
    assert unknown.status_code == 404 and unknown.json()["code"] == "community.society_not_found"
    no_id = await api.get("/v1/impact", params={"scope": "society"}, headers=admin)
    assert no_id.status_code == 422 and no_id.json()["code"] == "request.invalid"
    every = await api.get("/v1/impact", params={**params, "period": "all"}, headers=admin)
    assert every.status_code == 200 and every.json()["period"] == "all", every.text


async def test_a_society_has_no_monthly_numbers(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """All time only: with months, the all-time total less the months shown would give a month
    held back under 5 households."""
    api = clients()
    resident = await _as(api, google, PRIYA)
    params = {"scope": "society", "society_id": str(SEED_SOCIETY_ID)}
    this_month = datetime.now(IST).strftime("%Y-%m")

    for bad in (this_month, "2026-10", "2027-13", "March", "2027-1", "ALL"):
        answer = await api.get("/v1/impact", params={**params, "period": bad}, headers=resident)
        assert answer.status_code == 422, (bad, answer.text)
        assert answer.json()["code"] == "request.invalid", bad
    # A user's own numbers are hers, all time, as before
    mine = await api.get("/v1/impact", params={"scope": "me"}, headers=resident)
    assert mine.status_code == 200 and mine.json()["period"] == "all"


# --- a society: numbers and the k rule ------------------------------------------------------------


async def test_every_society_number_is_null_until_five_households_gave(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    sunita = await household_of(db_session, SUNITA)
    n1, n2, n3, n4 = [await move_in(db_session, n) for n in range(1, 5)]  # 5 homes in all
    # Three households give (two donations, a meal); a fourth was referred, which is no gift
    await donate(
        db_session,
        sunita,
        [line("clothes", "kg", 2.5), line("toys", "each", 2)],
        society_id=GREEN_VALLEY,
    )
    await donate(db_session, n1, [line("clothes", "kg", 1)], society_id=GREEN_VALLEY)
    await convert(db_session, sunita[0], n2, GREEN_VALLEY)
    await pledge(db_session, n3[0], 2)
    await pledge(db_session, n3[0], 1)  # the same household again: still one
    await donate(db_session, n4, [line("books", "each", 3)], society_id=GREEN_VALLEY)
    api = clients()
    auth = await _as(api, google, SUNITA)
    params = {"scope": "society", "society_id": str(GREEN_VALLEY)}

    four = await api.get("/v1/impact", params=params, headers=auth)

    assert four.status_code == 200, four.text
    body = four.json()
    assert body["homes_joined"] == 5  # five homes: enough to say so
    # Four households gave (Sunita, n1, n3, n4); n2's referral does not make a fifth
    assert body["contributors"] == "under_5"
    assert (body["items_donated"], body["kg_donated"], body["meals_donated"]) == (None, None, None)

    # The referred household gives a meal: now five gave, and all of it shows
    await pledge(db_session, n2[0], 1)
    five = (await api.get("/v1/impact", params=params, headers=auth)).json()
    assert five["contributors"] == "5+"
    # 4.5 -> 5 and 1 and 3 items; 3.5 kg; 2 + 1 + 1 meals
    assert (five["items_donated"], five["kg_donated"], five["meals_donated"]) == (9, 3.5, 4)
    assert not set(PERSONAL) & keys(five) and "household_id" not in keys(five)
    for trace in ("Sunita", "B-1", "household"):
        assert trace not in json.dumps(five)


async def test_five_referrals_and_one_donor_show_nothing(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """Referrals no longer pad the count: with them, five referred homes and one donor would put
    that one donor's giving on the society card and the public page."""
    sunita = await household_of(db_session, SUNITA)
    for n in range(1, 6):
        await convert(db_session, sunita[0], await move_in(db_session, n), GREEN_VALLEY)
    await donate(db_session, sunita, [line("clothes", "kg", 3)], society_id=GREEN_VALLEY)
    api = clients()
    params = {"scope": "society", "society_id": str(GREEN_VALLEY)}
    auth = await _as(api, google, SUNITA)

    body = (await api.get("/v1/impact", params=params, headers=auth)).json()
    facade = await community_api.society_impact(db_session, GREEN_VALLEY, with_hub=False)

    assert body["homes_joined"] == 6 and body["contributors"] == "under_5"
    assert (body["items_donated"], body["kg_donated"], body["meals_donated"]) == (None, None, None)
    assert facade is not None and facade.items_donated is None  # the public page's source
    public = (await api.get("/v1/public/societies/green-valley-heights-kharghar")).json()
    assert public["impact"] is None


async def test_homes_joined_is_null_under_five_and_every_gift_counts_all_time(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    sunita = await household_of(db_session, SUNITA)
    others = [await move_in(db_session, n) for n in range(1, 5)]
    last_month = datetime.now(UTC).replace(day=1) - timedelta(days=3)
    for who in (sunita, *others[:3]):
        await donate(db_session, who, [line("clothes", "kg", 2)], society_id=GREEN_VALLEY)
    # The fifth household's gift was last month's: all time, it counts
    await donate(
        db_session,
        others[3],
        [line("clothes", "kg", 4)],
        society_id=GREEN_VALLEY,
        verified_at=last_month,
    )
    api = clients()
    admin = await _as(api, google, NEHA)
    params = {"scope": "society", "society_id": str(GREEN_VALLEY)}

    every = (await api.get("/v1/impact", params=params, headers=admin)).json()

    assert (every["contributors"], every["items_donated"], every["kg_donated"]) == ("5+", 12, 12.0)
    assert every["homes_joined"] == 5 and every["period"] == "all"

    # Under five homes, the count itself is withheld (Sunrise Towers has Priya and Meena)
    sunrise = (
        await api.get(
            "/v1/impact", params={**params, "society_id": str(SEED_SOCIETY_ID)}, headers=admin
        )
    ).json()
    assert sunrise["homes_joined"] is None and sunrise["contributors"] == "under_5"


async def test_the_hub_block_counts_food_meals_and_partner_pay_under_the_k_rule(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_session.add(
        Ngo(
            id=SEED_NGO_ID,
            name="Annapurna",
            kind="food_bank",
            hub_ids=[SEED_HUB_ID],
            accepts=["items", "food", "meals"],
            verified=True,
        )
    )
    await db_session.flush()
    now = datetime.now(UTC)
    last_month = now.replace(day=1) - timedelta(days=3)
    for kind, tenths, meals, at in (
        ("food", 125, 0, now),
        ("food", 40, 0, last_month),
        ("meals", 0, 6, now),
        ("items", 30, 0, now),
    ):
        db_session.add(
            NgoHandoff(
                ngo_id=SEED_NGO_ID,
                hub_id=SEED_HUB_ID,
                kind=kind,
                kg_tenths=tenths,
                meals=meals,
                photo_keys=["handoff_photo/x/y.jpg"],
                handed_at=at,
                recorded_by="hub",
                recorded_by_user_id=uuid.uuid4(),
            )
        )
    await db_session.flush()
    api = clients()
    admin = await _as(api, google, NEHA)
    params = {"scope": "society", "society_id": str(SEED_SOCIETY_ID)}

    quiet = (await api.get("/v1/impact", params=params, headers=admin)).json()["hub"]
    assert quiet == {
        "food_saved_kg": 16.5,
        "meals_served": 6,
        "partners_earned_paise": None,
        "partners": "under_5",
    }
    # 12.5 kg this month and 4 kg last month: the society card is all time

    async def paid(*_a: object, **_k: object) -> tuple[int, int]:
        return 6, 1_580_000

    monkeypatch.setattr(impact, "hub_partners_paid", paid)
    full = (await api.get("/v1/impact", params=params, headers=admin)).json()["hub"]
    assert (full["partners_earned_paise"], full["partners"]) == (1_580_000, "5+")


async def test_partner_pay_is_the_hubs_partners_in_the_months_weeks(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[tuple[str, str]] = []
    mine = [uuid.UUID(int=i) for i in range(1, 7)]
    other, stranger = uuid.UUID(int=90), uuid.UUID(int=91)

    async def earnings(_s: AsyncSession, *, week_from: str, week_to: str) -> list[Any]:
        asked.append((week_from, week_to))
        return [
            billing_api.EarningFact(p, "2026-W46", "bag_delivered", 3, 100_00 * (i + 1))
            for i, p in enumerate([*mine, other, stranger])
        ] + [billing_api.EarningFact(mine[0], "2026-W47", "bag_delivered", 1, 50_00)]

    async def partners(_s: AsyncSession, *, ids: Any = None) -> list[Any]:
        return [
            partners_api.PartnerFact(p, SEED_HUB_ID, "rider", "active", "real") for p in mine
        ] + [
            partners_api.PartnerFact(other, OTHER_HUB, "rider", "active", "real"),
            partners_api.PartnerFact(stranger, SEED_HUB_ID, "rider", "active", "synthetic"),
        ]

    monkeypatch.setattr(billing_api, "earning_facts", earnings)
    monkeypatch.setattr(partners_api, "partner_facts", partners)

    count, paise = await impact.hub_partners_paid(
        db_session, SEED_HUB_ID, "real", date(2026, 11, 1)
    )

    assert asked == [("2026-W45", "2026-W49")]  # the Mondays of Nov 2, 9, 16, 23 and 30
    # Six partners of this hub and source; another hub's and the synthetic one's are left out
    assert (count, paise) == (6, sum(100_00 * i for i in range(1, 7)) + 50_00)


# --- the facade -----------------------------------------------------------------------------------


async def test_the_facade_applies_the_same_rules_for_the_public_page(
    db_session: AsyncSession,
) -> None:
    assert await community_api.society_impact(db_session, uuid.uuid4()) is None
    found = await community_api.society_impact(db_session, SEED_SOCIETY_ID, with_hub=False)
    assert found is not None
    assert found.hub is None and found.homes_joined is None and found.contributors == "under_5"
    assert (found.items_donated, found.kg_donated, found.meals_donated) == (None, None, None)
    with pytest.raises(ProblemError) as caught:
        await community_api.society_impact(db_session, SEED_SOCIETY_ID, "last-year")
    assert (caught.value.status, caught.value.code) == (422, "request.invalid")
    with pytest.raises(ProblemError):  # a month too: all time only
        await community_api.society_impact(db_session, SEED_SOCIETY_ID, "2026-10")
    named = await community_api.ngo_refs(db_session, [uuid.uuid4()])
    assert named == {}


async def test_periods_and_bands() -> None:
    impact.check_period("all")
    for month in ("2026-11", "2025-12", "last-year"):
        with pytest.raises(ProblemError):
            impact.check_period(month)
    assert impact.next_month(date(2026, 12, 1)) == date(2027, 1, 1)
    assert [impact.band(n) for n in (0, 4, 5, 9)] == ["under_5", "under_5", "5+", "5+"]
