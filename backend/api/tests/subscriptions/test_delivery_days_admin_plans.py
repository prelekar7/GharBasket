"""The hub's delivery days page and the admin plan list (M3 §3.5): every society of the hub in the
order the console shows them, whatever its status; every plan with its subscriber count; and who
may see them."""

import uuid

import httpx2
import pytest
import time_machine
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society
from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import Plan, SocietyDeliveryDay, Subscription
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_cycles import MEENA, NOW, PRIYA, VIKRAM, as_user, subscribe
from .test_hub_cutoff import KAVITA, NEHA, SANJAY

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

DAYS = f"/v1/hubs/{SEED_HUB_ID}/delivery-days"
PLANS = "/v1/admin/plans"


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def _society(db: AsyncSession, name: str) -> Society:
    return (await db.execute(select(Society).where(Society.name == name))).scalar_one()


# --- hub delivery days ----------------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_the_hub_lists_every_society_live_first_with_its_delivery_days(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await as_user(api, google, VIKRAM)
    sunrise = await _society(db_session, "Sunrise Towers")
    sunrise_id = sunrise.id
    slot_id = (await service.delivery_days(db_session, sunrise_id))[0].slot_id
    # Tuesday and Friday for Sunrise; none for Shree Ganesh; Krishna Kunj paused.
    await service.set_delivery_days(
        db_session, sunrise_id, SEED_HUB_ID, [(4, slot_id), (1, slot_id)]
    )
    bare = await _society(db_session, "Shree Ganesh CHS")
    await db_session.execute(
        delete(SocietyDeliveryDay).where(SocietyDeliveryDay.society_id == bare.id)
    )
    await db_session.execute(
        update(Society).where(Society.name == "Krishna Kunj").values(status="paused")
    )
    await db_session.commit()

    answer = await api.get(DAYS, headers=manager)

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "no-store"
    body = answer.json()
    assert body["hub_id"] == str(SEED_HUB_ID)
    assert [(s["name"], s["status"], len(s["items"])) for s in body["items"]] == [
        ("Sunrise Towers", "live", 2),
        ("Green Valley Heights", "unlocking", 1),
        ("Shree Ganesh CHS", "unlocking", 0),
        ("Panchavati Residency", "prospect", 1),
        ("Krishna Kunj", "paused", 1),
    ]
    first = body["items"][0]
    assert first["society_id"] == str(sunrise_id)
    assert first["area"] == "Sector 20, Kharghar"
    assert [d["weekday"] for d in first["items"]] == [1, 4]  # Tuesday, Friday (0 = Monday)
    slot = first["items"][0]["slot"]
    assert slot["id"] == str(slot_id) and set(slot) == {"id", "label", "start", "end"}
    assert slot["start"] == "07:00:00" and slot["end"] == "09:00:00"
    # A society with no hub (the seed's Sai Darshan) is not on any hub's page.
    assert "Sai Darshan Apartments" not in {s["name"] for s in body["items"]}


async def test_who_may_see_a_hubs_delivery_days(clients: ClientFactory, google: FakeGoogle) -> None:
    api = clients()
    customer, _ = await as_user(api, google, PRIYA)
    manager, _ = await as_user(api, google, VIKRAM)
    owner, _ = await as_user(api, google, SANJAY)
    packer, _ = await as_user(api, google, KAVITA)
    admin, _ = await as_user(api, google, NEHA)
    elsewhere = f"/v1/hubs/{uuid.uuid4()}/delivery-days"

    refused = await api.get(DAYS, headers=customer)
    others_hub = await api.get(elsewhere, headers=manager)
    anonymous = await api.get(DAYS)
    allowed = [await api.get(DAYS, headers=h) for h in (manager, owner, packer, admin)]
    empty = await api.get(elsewhere, headers=admin)

    assert code(refused) == (403, "auth.forbidden")
    assert code(others_hub) == (403, "auth.forbidden")
    assert anonymous.status_code == 401
    assert [a.status_code for a in allowed] == [200, 200, 200, 200]
    assert all(len(a.json()["items"]) == 5 for a in allowed)
    assert empty.status_code == 200 and empty.json()["items"] == []


# --- the admin plan list ------------------------------------------------------------


async def test_the_admin_plan_list_has_every_plan_with_its_live_subscribers(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    admin, _ = await as_user(api, google, NEHA)
    manager, _ = await as_user(api, google, VIKRAM)
    await subscribe(api, priya)
    await subscribe(api, meena)
    db_session.add_all(
        [
            Plan(
                code="tiffin",
                names={"en": "Tiffin", "hi": "टिफ़िन", "mr": "टिफिन"},
                fee_paise=9900,
                features=["daily_tiffin"],
                active=False,
            ),
            Plan(
                code="salad",
                names={"en": "Salad", "hi": "सलाद", "mr": "सॅलड"},
                fee_paise=4900,
                features=[],
                active=True,
            ),
        ]
    )
    await db_session.commit()

    both = await api.get(PLANS, headers=admin)
    await db_session.execute(
        update(Subscription).where(Subscription.status == "active").values(status="ended")
    )
    await db_session.commit()
    ended = await api.get(PLANS, headers={**admin, "Accept-Language": "hi"})
    public = await api.get("/v1/plans", headers=admin)
    refused = await api.get(PLANS, headers=manager)
    customer = await api.get(PLANS, headers=priya)

    assert both.status_code == 200, both.text
    assert both.headers["Cache-Control"] == "no-store"
    assert [
        (p["code"], p["state"], p["subscriber_count"], p["fee_paise"]) for p in both.json()["items"]
    ] == [
        ("autopilot", "live", 2, 0),
        ("salad", "live", 0, 4900),  # nobody on it: 0, never null
        ("tiffin", "draft", 0, 9900),
    ]
    assert both.json()["items"][0]["name"] == "Kitchen Autopilot"
    assert both.json()["items"][0]["features"] == ["weekly_box", "budget_lock"]
    # Ended subscriptions do not count, and the names follow Accept-Language.
    assert [p["subscriber_count"] for p in ended.json()["items"]] == [0, 0, 0]
    assert ended.json()["items"][0]["name"] == "किचन ऑटोपायलट"
    # The public list still has live plans only.
    assert [p["code"] for p in public.json()["items"]] == ["autopilot", "salad"]
    assert code(refused) == (403, "auth.forbidden")
    assert code(customer) == (403, "auth.forbidden")
