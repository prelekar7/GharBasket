"""Society Captains (M9 §3.2, ADR-0026 §4): automatic and hub appointments, ending, the
consumers of M8's events, M1's captain role, the card and the facade."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.community import captains
from gharbasket.modules.community.models import Captain
from gharbasket.modules.identity.models import Household, UserRole
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners import service as partners_service
from gharbasket.modules.partners.models import Partner
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID, SOCIETIES

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .conftest import deliver

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("captain_world")]

PRIYA = "+919800001001"  # Sunrise Towers, not a partner
SUNITA = "+919800001002"  # Green Valley Heights
MEENA = "+919800001004"  # captain of Sunrise Towers (seed)
LATA = "+919800001005"
VIKRAM = "+919800001008"  # hub manager of the seed hub
NEHA = "+919800001011"  # admin
SAVITA = "+919800001012"  # captain of Krishna Kunj (seed)
KRISHNA_KUNJ = next(s.id for s in SOCIETIES if s.slug == "krishna-kunj-new-panvel")
GREEN_VALLEY = next(s.id for s in SOCIETIES if s.slug == "green-valley-heights-kharghar")
SHREE_GANESH = next(s.id for s in SOCIETIES if s.slug == "shree-ganesh-chs-kharghar")
ASSIGNED_KEYS = {"captain_id", "society_id", "hub_id", "user_id", "partner_id", "since"}
ENDED_KEYS = {"captain_id", "society_id", "hub_id", "user_id", "partner_id", "until", "reason"}


async def _user(db: AsyncSession, phone: str) -> uuid.UUID:
    user, _ = await find_or_create_user(db, phone)
    return user.id


async def _as(api: Any, google: FakeGoogle, phone: str) -> dict[str, str]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"])


async def _active(db: AsyncSession, society_id: uuid.UUID) -> Captain:
    captain = await captains.active_for_society(db, society_id)
    assert captain is not None
    return captain


async def _payloads(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == event_type).order_by(OutboxEvent.id)
    )
    return [event.payload for event in rows.scalars()]


async def _captain_roles(db: AsyncSession) -> set[tuple[uuid.UUID, uuid.UUID | None, str]]:
    rows = await db.execute(select(UserRole).where(UserRole.role == "captain"))
    return {(r.user_id, r.scope_id, r.status) for r in rows.scalars()}


async def _captain_partner(db: AsyncSession, phone: str, **kwargs: Any) -> Partner:
    """A verified captain partner of the seed hub who asked for no society (no auto-appointment)."""
    return await partners_service.verify(
        db,
        user_id=await _user(db, phone),
        kind="captain",
        hub_id=SEED_HUB_ID,
        display_name=kwargs.pop("display_name", "Priya tai"),
        **kwargs,
    )


async def test_the_seed_captains_are_appointed_and_get_their_role(
    db_session: AsyncSession,
) -> None:
    meena, savita = await _user(db_session, MEENA), await _user(db_session, SAVITA)

    sunrise, krishna = (
        await _active(db_session, SEED_SOCIETY_ID),
        await _active(db_session, KRISHNA_KUNJ),
    )

    assert (sunrise.user_id, sunrise.appointed_by, sunrise.hub_id) == (meena, "auto", SEED_HUB_ID)
    assert krishna.user_id == savita and krishna.until is None
    assigned = await _payloads(db_session, community_api.CAPTAIN_ASSIGNED)
    assert len(assigned) == 2
    # Never a name, flat or phone in the event.
    assert all(set(p) == ASSIGNED_KEYS | {"appointed_by"} for p in assigned)
    assert await _captain_roles(db_session) == {
        (meena, SEED_SOCIETY_ID, "verified"),
        (savita, KRISHNA_KUNJ, "verified"),
    }


async def test_a_duplicate_verified_event_appoints_once(db_session: AsyncSession) -> None:
    (event,) = (
        await db_session.execute(
            select(OutboxEvent).where(
                OutboxEvent.event_type == partners_api.VERIFIED,
                OutboxEvent.payload["user_id"].astext == str(await _user(db_session, MEENA)),
            )
        )
    ).scalars()
    sunrise = await _active(db_session, SEED_SOCIETY_ID)
    await captains.end(db_session, sunrise, reason="stepped_down", note=None, by_user_id=None)

    await captains.on_partner_verified(db_session, event)

    assert await captains.active_for_society(db_session, SEED_SOCIETY_ID) is None
    rows = (await db_session.execute(select(Captain))).scalars().all()
    assert len(rows) == 2


async def test_auto_appointment_needs_a_vacant_society_a_free_resident_captain(
    db_session: AsyncSession,
) -> None:
    # Priya lives in Sunrise Towers, which has Meena: her request waits.
    priya = await _captain_partner(db_session, PRIYA, requested_society_id=SEED_SOCIETY_ID)
    # Sunita lives in Green Valley, which has no captain: she is appointed.
    sunita = await _captain_partner(
        db_session, SUNITA, display_name="Sunita tai", requested_society_id=GREEN_VALLEY
    )
    # Lata has no household, so she lives in no society: her request for a vacant one waits.
    lata_captain = await _captain_partner(
        db_session, LATA, display_name="Lata tai", requested_society_id=SHREE_GANESH
    )
    # A kitchen is never appointed, and a bad payload never raises.
    await partners_service.verify(
        db_session,
        user_id=await _user(db_session, LATA),
        kind="kitchen",
        hub_id=SEED_HUB_ID,
        display_name="Lata tai",
    )
    for payload in ({}, {"kind": "captain", "partner_id": "x", "requested_society_id": None}):
        await captains.on_partner_verified(
            db_session, OutboxEvent(event_type=partners_api.VERIFIED, payload=payload)
        )
    await deliver(db_session)

    assert await captains.active_for_partner(db_session, priya.id) is None
    assert await captains.active_for_partner(db_session, lata_captain.id) is None
    sunita_captain = await captains.active_for_partner(db_session, sunita.id)
    assert sunita_captain is not None and sunita_captain.society_id == GREEN_VALLEY
    assert len((await db_session.execute(select(Captain))).scalars().all()) == 3


async def test_hub_staff_see_the_history_newest_first(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram, neha = await _as(api, google, VIKRAM), await _as(api, google, NEHA)
    priya_auth = await _as(api, google, PRIYA)
    priya = await _captain_partner(db_session, PRIYA)
    url = f"/v1/societies/{SEED_SOCIETY_ID}/captains"
    replaced = await api.post(
        url,
        json={"partner_id": str(priya.id), "replace": True},
        headers={**vikram, "Idempotency-Key": "replace-meena"},
    )
    assert replaced.status_code == 201, replaced.text

    history = await api.get(url, headers=neha)
    active = await api.get(url, params={"status": "active"}, headers=vikram)
    await db_session.commit()
    by_customer = await api.get(url, headers=priya_auth)

    items = history.json()["items"]
    assert [(c["display_name"], c["status"], c["end_reason"]) for c in items] == [
        ("Priya tai", "active", None),
        ("Meena tai", "ended", "replaced"),
    ]
    assert items[0]["appointed_by"] == "hub" and items[0]["photo_url"] is None
    assert not {"flat", "phone_e164", "name"} & set(items[0])
    assert [c["display_name"] for c in active.json()["items"]] == ["Priya tai"]
    assert by_customer.status_code == 403


async def test_replace_ends_then_appoints_and_moves_the_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    priya = await _captain_partner(db_session, PRIYA)
    priya_id, priya_user = priya.id, priya.user_id
    meena = await _active(db_session, SEED_SOCIETY_ID)
    meena_id, meena_user = meena.id, meena.user_id
    url = f"/v1/societies/{SEED_SOCIETY_ID}/captains"
    await db_session.commit()  # the refusal below rolls the shared session back

    taken = await api.post(
        url, json={"partner_id": str(priya_id)}, headers={**vikram, "Idempotency-Key": "k1"}
    )
    body = {"partner_id": str(priya_id), "replace": True}
    key = {**vikram, "Idempotency-Key": "k2"}
    replaced = await api.post(url, json=body, headers=key)
    replayed = await api.post(url, json=body, headers=key)

    assert (taken.status_code, taken.json()["code"]) == (409, "community.captain_exists")
    assert taken.json()["captain_id"] == str(meena_id)
    assert replaced.status_code == 201, replaced.text
    assert replayed.headers.get("Idempotent-Replayed") == "true"
    assert replaced.json()["status"] == "active" and replaced.json()["user_id"] == str(priya_user)
    ended = await _payloads(db_session, community_api.CAPTAIN_ENDED)
    assert [set(p) for p in ended] == [ENDED_KEYS]
    assert ended[0]["reason"] == "replaced" and ended[0]["captain_id"] == str(meena_id)
    events = (
        (
            await db_session.execute(
                select(OutboxEvent.event_type)
                .where(OutboxEvent.event_type.like("community.%"))
                .order_by(OutboxEvent.id)
            )
        )
        .scalars()
        .all()
    )
    assert events[-2:] == [community_api.CAPTAIN_ENDED, community_api.CAPTAIN_ASSIGNED]

    await deliver(db_session)
    roles = await _captain_roles(db_session)
    assert (priya_user, SEED_SOCIETY_ID, "verified") in roles
    assert not any(user == meena_user for user, _, _ in roles)


async def test_who_can_be_appointed(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    stranger_phone = "+919800000777"
    stranger_id = await _user(db_session, stranger_phone)
    db_session.add(
        UserRole(user_id=stranger_id, role="hub_manager", scope_type="hub", scope_id=uuid.uuid4())
    )
    await db_session.flush()
    stranger = await _as(api, google, stranger_phone)
    sunita = await _captain_partner(db_session, SUNITA, display_name="Sunita tai")
    kitchen = await partners_service.verify(
        db_session,
        user_id=await _user(db_session, LATA),
        kind="kitchen",
        hub_id=SEED_HUB_ID,
        display_name="Lata tai",
    )
    meena = await _active(db_session, SEED_SOCIETY_ID)
    # Meena moves to Krishna Kunj (still Sunrise Towers' captain): Krishna Kunj has Savita.
    await db_session.execute(
        update(Household)
        .where(Household.owner_user_id == meena.user_id)
        .values(society_id=KRISHNA_KUNJ)
    )
    sunita_id, kitchen_id, meena_partner = sunita.id, kitchen.id, meena.partner_id
    await db_session.commit()

    async def appoint(society: uuid.UUID, partner: uuid.UUID, auth: dict[str, str]) -> Any:
        return await api.post(
            f"/v1/societies/{society}/captains",
            json={"partner_id": str(partner), "replace": True},
            headers={**auth, "Idempotency-Key": str(uuid.uuid4())},
        )

    not_resident = await appoint(SEED_SOCIETY_ID, sunita_id, vikram)
    a_kitchen = await appoint(SEED_SOCIETY_ID, kitchen_id, vikram)
    unknown = await appoint(SEED_SOCIETY_ID, uuid.uuid4(), vikram)
    elsewhere = await appoint(KRISHNA_KUNJ, meena_partner, vikram)
    other_hub = await appoint(SEED_SOCIETY_ID, sunita_id, stranger)
    no_society = await appoint(uuid.uuid4(), sunita_id, vikram)

    assert not_resident.json()["code"] == "community.captain_not_resident"
    assert a_kitchen.json()["code"] == unknown.json()["code"] == "community.partner_not_eligible"
    assert (elsewhere.status_code, elsewhere.json()["code"]) == (409, "community.already_captain")
    assert other_hub.status_code == no_society.status_code == 404
    assert other_hub.json()["code"] == "community.society_not_found"


async def test_a_suspended_captain_cannot_be_appointed(db_session: AsyncSession) -> None:
    priya = await _captain_partner(db_session, PRIYA)
    await partners_service.suspend(
        db_session, priya, reason_code="other", note="x", by_user_id=None
    )

    with pytest.raises(ProblemError) as refused:
        await captains.appoint(
            db_session,
            SEED_SOCIETY_ID,
            priya.id,
            replace=True,
            appointed_by="hub",
            by_user_id=None,
        )
    assert refused.value.code == "community.partner_not_eligible"


async def test_a_lost_race_answers_409_from_the_unique_index(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    priya = await _captain_partner(db_session, PRIYA)
    meena = await _active(db_session, SEED_SOCIETY_ID)
    real = captains.active_for_society
    meena_id = meena.id
    calls = 0

    async def blind(session: AsyncSession, society_id: uuid.UUID) -> Captain | None:
        nonlocal calls
        calls += 1  # the check before the insert misses Meena; the one after the error sees her
        return None if calls == 1 else await real(session, society_id)

    monkeypatch.setattr(captains, "active_for_society", blind)

    with pytest.raises(ProblemError) as lost:
        await captains.appoint(
            db_session,
            SEED_SOCIETY_ID,
            priya.id,
            replace=False,
            appointed_by="hub",
            by_user_id=None,
        )
    assert (lost.value.status, lost.value.code) == (409, "community.captain_exists")
    assert lost.value.extra == {"captain_id": str(meena_id)}


async def test_end_and_end_again(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram = await _as(api, google, VIKRAM)
    savita = await _active(db_session, KRISHNA_KUNJ)
    url = f"/v1/captains/{savita.id}/end"

    ended = await api.post(
        url,
        json={"reason": "stepped_down", "note": " Moving to Pune "},
        headers={**vikram, "Idempotency-Key": "e1"},
    )
    await db_session.commit()
    again = await api.post(
        url, json={"reason": "hub_decision"}, headers={**vikram, "Idempotency-Key": "e2"}
    )
    unknown = await api.post(
        f"/v1/captains/{uuid.uuid4()}/end",
        json={"reason": "hub_decision"},
        headers={**vikram, "Idempotency-Key": "e3"},
    )
    suspended_reason = await api.post(
        url, json={"reason": "suspended"}, headers={**vikram, "Idempotency-Key": "e4"}
    )

    assert ended.status_code == 200, ended.text
    assert ended.json()["status"] == "ended" and ended.json()["until"] is not None
    assert ended.json()["end_reason"] == "stepped_down"
    await db_session.refresh(savita)
    assert savita.note == "Moving to Pune"
    assert (again.status_code, again.json()["code"]) == (409, "community.captain_already_ended")
    assert (unknown.status_code, unknown.json()["code"]) == (404, "community.captain_not_found")
    assert suspended_reason.status_code == 422
    await deliver(db_session)
    assert (savita.user_id, KRISHNA_KUNJ, "verified") not in await _captain_roles(db_session)


async def test_suspension_ends_the_appointment_and_reinstating_does_not_reappoint(
    db_session: AsyncSession,
) -> None:
    meena = await _active(db_session, SEED_SOCIETY_ID)
    partner = await db_session.get(Partner, meena.partner_id)
    assert partner is not None

    await partners_service.suspend(
        db_session, partner, reason_code="conduct", note="x", by_user_id=None
    )
    await deliver(db_session)
    await db_session.refresh(meena)
    assert (meena.status, meena.end_reason) == ("ended", "suspended")
    assert (meena.user_id, SEED_SOCIETY_ID, "verified") not in await _captain_roles(db_session)

    await partners_service.reinstate(db_session, partner, note=None, by_user_id=None)
    await deliver(db_session)
    assert await captains.active_for_society(db_session, SEED_SOCIETY_ID) is None


async def test_the_card_goes_to_residents_only(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    priya, sunita = await _as(api, google, PRIYA), await _as(api, google, SUNITA)
    url = f"/v1/societies/{SEED_SOCIETY_ID}"

    resident = await api.get(url, headers=priya)
    outsider = await api.get(url, headers=sunita)
    listed = await api.get("/v1/societies", params={"q": "sunrise"}, headers=priya)

    assert resident.json()["captain"] == {"display_name": "Meena tai", "photo_url": None}
    assert outsider.json()["captain"] is None
    assert "captain" not in listed.json()["items"][0]


async def test_the_facade_and_the_export(db_session: AsyncSession) -> None:
    meena = await _active(db_session, SEED_SOCIETY_ID)

    ref = await community_api.captain_for_society(db_session, SEED_SOCIETY_ID)
    assert ref == community_api.CaptainRef(meena.id, meena.user_id, meena.partner_id, "Meena tai")
    assert await community_api.captain_for_society(db_session, GREEN_VALLEY) is None
    assert await community_api.captain_card(
        db_session, SEED_SOCIETY_ID
    ) == community_api.CaptainCard("Meena tai", None)
    assert await community_api.captain_card(db_session, GREEN_VALLEY) is None
    assert await community_api.is_active_captain(db_session, meena.user_id, SEED_SOCIETY_ID)
    assert not await community_api.is_active_captain(db_session, meena.user_id, KRISHNA_KUNJ)
    exported = await community_api.export_for_user(db_session, meena.user_id)
    assert exported == {
        "captain_appointments": [
            {
                "society_id": SEED_SOCIETY_ID,
                "society": "Sunrise Towers",
                "status": "active",
                "since": meena.since,
                "until": None,
            }
        ],
        "donations": [],
        "referral_code": None,
        "referrals_made": [],
        "referred": None,
        "meal_pledges": [],
    }
