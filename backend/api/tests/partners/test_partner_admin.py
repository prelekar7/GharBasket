"""A partner's own display name (M8 §3.3) and the hub's rate card (§3.6), with the card's rate
steering the assignment round and the preferred cook coming first (§3.4, ADR-0027)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx2
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.orders.models import Order
from gharbasket.modules.partners import kitchens, rates
from gharbasket.modules.partners.models import Assignment
from gharbasket.platform.models import AuditEntry
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
from .conftest import LATA, MEENA, NEHA, PRIYA, SANJAY, SUNITA, VIKRAM, as_user, other_hub_manager
from .tiffins import EVENING, T, kitchen, tag, tiffin_order

pytestmark = [pytest.mark.anyio, pytest.mark.services]

RATES = f"/v1/hubs/{SEED_HUB_ID}/partner-rates"
TODAY = date(2027, 1, 10)


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


@pytest.fixture
async def world(db_session: AsyncSession) -> None:
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


# --- PATCH /v1/partners/{id} --------------------------------------------------------------------


@pytest.mark.usefixtures("world")
async def test_a_partner_renames_herself_with_if_match(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    meena, _ = await as_user(api, google, MEENA)
    (me,) = (await api.get("/v1/partners/me", headers=meena)).json()["items"]
    url = f"/v1/partners/{me['id']}"

    renamed = await api.patch(
        url, json={"display_name": "  Meena   tai "}, headers={**meena, "If-Match": '"1"'}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "Meena tai" and renamed.json()["version"] == 1
    # The seeded name was already "Meena tai": nothing changed, so the version stayed.
    changed = await api.patch(
        url, json={"display_name": "Meena didi"}, headers={**meena, "If-Match": '"1"'}
    )
    assert changed.status_code == 200
    assert (changed.json()["display_name"], changed.json()["version"]) == ("Meena didi", 2)
    assert changed.headers["ETag"] == '"2"'

    stale = await api.patch(
        url, json={"display_name": "Meena ji"}, headers={**meena, "If-Match": '"1"'}
    )
    missing = await api.patch(url, json={"display_name": "Meena ji"}, headers=meena)
    short = await api.patch(url, json={"display_name": "M"}, headers={**meena, "If-Match": '"2"'})
    blank = await api.patch(url, json={"display_name": "   "}, headers={**meena, "If-Match": '"2"'})
    assert code(stale) == (412, "request.version_mismatch")
    assert code(missing) == (428, "request.precondition_required")
    assert code(short) == (422, "partners.display_name_invalid")
    assert code(blank) == (422, "partners.display_name_invalid")


@pytest.mark.usefixtures("world")
async def test_only_the_partner_herself_may_rename(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    meena, _ = await as_user(api, google, MEENA)
    (me,) = (await api.get("/v1/partners/me", headers=meena)).json()["items"]
    vikram, _ = await as_user(api, google, VIKRAM)
    priya, _ = await as_user(api, google, PRIYA)
    body = {"display_name": "Someone else"}

    # Hub staff read her profile but never edit it; a stranger finds nothing.
    staff = await api.patch(
        f"/v1/partners/{me['id']}", json=body, headers={**vikram, "If-Match": '"1"'}
    )
    stranger = await api.patch(
        f"/v1/partners/{me['id']}", json=body, headers={**priya, "If-Match": '"1"'}
    )
    unknown = await api.patch(
        f"/v1/partners/{uuid.uuid4()}", json=body, headers={**meena, "If-Match": '"1"'}
    )
    assert code(staff) == (404, "partners.partner_not_found")
    assert code(stranger) == (404, "partners.partner_not_found")
    assert code(unknown) == (404, "partners.partner_not_found")
    # `me` is the read-only route: no PATCH there, not a partner id to look up.
    assert (await api.patch("/v1/partners/me", json=body, headers=meena)).status_code == 405


# --- the rate card -------------------------------------------------------------------------------


@pytest.fixture
def today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kitchens, "now", lambda: datetime(2027, 1, 10, 6, 0, tzinfo=UTC))


@pytest.mark.usefixtures("world", "today")
async def test_every_hub_starts_with_the_owners_rates_and_staff_read_them(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    expected = [
        {"kind": "kitchen", "basis": "tiffin_portion", "amount_paise": 6000},
        {"kind": "captain", "basis": "bag_delivered", "amount_paise": 600},
        {"kind": "rider", "basis": "dispatched_order", "amount_paise": 800},
        {"kind": "packer", "basis": "packed_order", "amount_paise": 400},
    ]
    for phone in (VIKRAM, SANJAY, NEHA):
        headers, _ = await as_user(api, google, phone)
        card = await api.get(RATES, headers=headers)
        assert card.status_code == 200, card.text
        assert card.headers["Cache-Control"] == "no-store"
        assert card.json()["hub_id"] == str(SEED_HUB_ID)
        assert [
            {k: v for k, v in r.items() if k != "valid_from"} for r in card.json()["rates"]
        ] == (
            sorted(
                expected, key=lambda r: ["kitchen", "captain", "rider", "packer"].index(r["kind"])
            )
        )
    priya, _ = await as_user(api, google, PRIYA)
    other = await other_hub_manager(api, google, db_session)
    assert code(await api.get(RATES, headers=priya)) == (403, "auth.forbidden")
    assert code(await api.get(RATES, headers=other)) == (403, "auth.forbidden")
    neha, _ = await as_user(api, google, NEHA)
    assert code(await api.get(f"/v1/hubs/{uuid.uuid4()}/partner-rates", headers=neha)) == (
        404,
        "hub_ops.hub_not_found",
    )


@pytest.mark.usefixtures("world", "today")
async def test_only_an_admin_sets_rates_from_today_and_the_old_rate_serves_until_then(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha, neha_id = await as_user(api, google, NEHA)
    vikram, _ = await as_user(api, google, VIKRAM)
    tomorrow = TODAY + timedelta(days=1)
    change = {
        "rates": [
            {
                "kind": "kitchen",
                "basis": "tiffin_portion",
                "amount_paise": 6500,
                "valid_from": TODAY.isoformat(),
            },
            {
                "kind": "captain",
                "basis": "bag_delivered",
                "amount_paise": 700,
                "valid_from": tomorrow.isoformat(),
            },
        ]
    }

    assert code(await api.put(RATES, json=change, headers=vikram)) == (403, "auth.forbidden")
    done = await api.put(RATES, json=change, headers=neha)
    assert done.status_code == 200, done.text
    card = {(r["kind"], r["valid_from"]): r["amount_paise"] for r in done.json()["rates"]}
    assert card[("kitchen", TODAY.isoformat())] == 6500
    assert card[("captain", rates.DEFAULT_FROM.isoformat())] == 600  # in force today
    assert card[("captain", tomorrow.isoformat())] == 700  # scheduled
    assert await rates.rate_on(db_session, SEED_HUB_ID, "captain", TODAY) == 600
    assert await rates.rate_on(db_session, SEED_HUB_ID, "captain", tomorrow) == 700
    assert await rates.rate_on(db_session, SEED_HUB_ID, "rider", tomorrow) == 800

    # The same date again replaces the amount; the answer is the card as vikram reads it.
    again = {"rates": [change["rates"][0] | {"amount_paise": 6600}]}
    assert (await api.put(RATES, json=again, headers=neha)).status_code == 200
    seen = (await api.get(RATES, headers=vikram)).json()["rates"]
    assert [r["amount_paise"] for r in seen if r["kind"] == "kitchen"] == [6600]
    audit = (
        (
            await db_session.execute(
                select(AuditEntry).where(AuditEntry.action == "partners.rates_set")
            )
        )
        .scalars()
        .all()
    )
    assert len(audit) == 2 and audit[0].actor_user_id == neha_id
    assert audit[0].before is not None and audit[0].after is not None


@pytest.mark.usefixtures("world", "today")
async def test_rates_must_match_the_kind_and_start_today_or_later(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    neha, _ = await as_user(api, google, NEHA)

    def body(**rate: object) -> dict[str, object]:
        base = {
            "kind": "rider",
            "basis": "dispatched_order",
            "amount_paise": 900,
            "valid_from": TODAY.isoformat(),
        }
        return {"rates": [base | rate]}

    assert code(await api.put(RATES, json=body(basis="packed_order"), headers=neha)) == (
        422,
        "partners.rate_invalid",
    )
    past = (TODAY - timedelta(days=1)).isoformat()
    assert code(await api.put(RATES, json=body(valid_from=past), headers=neha)) == (
        422,
        "partners.rate_invalid",
    )
    twice = {"rates": body()["rates"] * 2}  # type: ignore[operator]
    assert code(await api.put(RATES, json=twice, headers=neha)) == (422, "partners.rate_invalid")
    assert code(await api.put(RATES, json=body(amount_paise=-1), headers=neha))[0] == 422
    assert code(await api.put(RATES, json={"rates": []}, headers=neha))[0] == 422
    assert code(
        await api.put(f"/v1/hubs/{uuid.uuid4()}/partner-rates", json=body(), headers=neha)
    ) == (
        404,
        "hub_ops.hub_not_found",
    )


# --- the round uses the card, and the preferred cook goes first ---------------------------------


@pytest.mark.usefixtures("world")
async def test_the_round_reads_the_hubs_kitchen_rate_and_the_preferred_cook_goes_first(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    clock: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    neha, _ = await as_user(api, google, NEHA)
    lata = await kitchen(db_session, LATA, "Lata tai", lunch=5)
    sunita = await kitchen(db_session, SUNITA, "Sunita tai", lunch=5)
    booked = await tiffin_order(db_session, PRIYA, 2)
    other = await tiffin_order(db_session, MEENA, 1)
    # Priya booked Sunita's menu: Sunita is the preferred cook, though Lata is as near.
    await db_session.execute(
        update(Order).where(Order.id == booked).values(preferred_partner_id=sunita)
    )
    await db_session.commit()
    card = {
        "rates": [
            {
                "kind": "kitchen",
                "basis": "tiffin_portion",
                "amount_paise": 6500,
                "valid_from": T.isoformat(),
            }
        ]
    }
    assert (await api.put(RATES, json=card, headers=neha)).status_code == 200
    seen: list[intel_api.AssignRequest] = []

    async def no_answer(
        http: httpx2.AsyncClient, settings: Any, request: intel_api.AssignRequest, **kw: Any
    ) -> None:
        seen.append(request)

    monkeypatch.setattr(intel_api, "assign_kitchens", no_answer)
    run = await api.post(
        f"/v1/hubs/{SEED_HUB_ID}/tiffin-assignments/run",
        json={"date": T.isoformat()},
        headers={**vikram, **key()},
    )

    assert run.status_code == 200, run.text
    # Only the order nobody preferred reaches the solver, with the card's rate and Sunita's
    # rolling earnings already raised by what she took (2 portions at the card's rate).
    (request,) = seen
    assert [o.id for o in request.orders] == [str(other)]
    assert request.paise_per_portion == 6500
    assert {k.id: k.earnings_paise for k in request.kitchens} == {
        str(lata): 0,
        str(sunita): 13_000,
    }
    mine = {
        a.partner_id: a.portions for a in (await db_session.execute(select(Assignment))).scalars()
    }
    assert mine[sunita] == 2 and mine.get(lata, 0) + mine[sunita] == 3
