"""Tiffin menus to book (M4 §3.5, M2 §4): approved menus of the caller's hub, tomorrow to a week
ahead, until their date's cut-off."""

import uuid
from datetime import UTC, date, datetime

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.orders import service
from gharbasket.modules.partners import api as partners_api
from gharbasket.tools.seed import seed_partners

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_orders import PRIYA, VIKRAM, as_user
from .tiffin_menus import menu

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # Sat 15:30 IST
TOMORROW = date(2026, 10, 11)
MEENA = "+919800001004"  # a seeded partner, standing in for a kitchen's card


@time_machine.travel(NOW, tick=False)
async def test_a_household_sees_its_hubs_approved_menus_until_their_cut_off(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await seed_partners(db_session)
    meena_user, _ = await find_or_create_user(db_session, MEENA)
    cook = await partners_api.partner_for_user(db_session, meena_user.id, "captain")
    assert cook is not None
    lunch = (await menu(db_session, TOMORROW, partner_id=cook.id)).id
    dinner = (await menu(db_session, date(2026, 10, 14), meal="dinner")).id
    await menu(db_session, TOMORROW, status="submitted")
    await menu(db_session, TOMORROW, status="sent_back")
    await menu(db_session, TOMORROW, status="withdrawn")
    await menu(db_session, TOMORROW, hub_id=uuid.uuid4())  # another hub
    await menu(db_session, date(2026, 10, 18))  # beyond a week
    await db_session.commit()
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)

    listed = await api.get("/v1/tiffin-menus", headers={**priya, "Accept-Language": "hi"})
    one = await api.get("/v1/tiffin-menus?date=2026-10-14&meal=dinner", headers=priya)
    today = await api.get("/v1/tiffin-menus?date=2026-10-10", headers=priya)
    bad_meal = await api.get("/v1/tiffin-menus?meal=brunch", headers=priya)
    no_household = await api.get("/v1/tiffin-menus", headers=vikram)
    # 21:00 IST: tomorrow's menus are past their cut-off (the token stays valid).
    monkeypatch.setattr(service, "now", lambda: datetime(2026, 10, 10, 15, 30, tzinfo=UTC))
    closed = await api.get("/v1/tiffin-menus", headers=priya)

    assert listed.status_code == 200, listed.text
    assert listed.headers["Cache-Control"] == "no-store"
    items = listed.json()["items"]
    assert [i["id"] for i in items] == [str(lunch), str(dinner)]
    first = items[0]
    assert (first["date"], first["meal"], first["price_paise"]) == ("2026-10-11", "lunch", 11_000)
    assert first["order_by"] == "2026-10-10T15:30:00Z"
    assert first["kitchen"] == {
        "partner_id": str(cook.id),
        "display_name": cook.display_name,
        "photo_url": None,
    }
    recipes = (await catalog_api.tiffin_menu(db_session, lunch)).dishes  # type: ignore[union-attr]
    assert [d["recipe_id"] for d in first["dishes"]] == [str(rid) for rid, _ in recipes]
    assert first["dishes"][0]["name"] == recipes[0][1]["hi"]
    assert items[1]["kitchen"]["display_name"] == ""  # no partner record behind this one
    assert [i["id"] for i in one.json()["items"]] == [str(dinner)]
    assert (today.status_code, today.json()["code"]) == (422, "orders.date_out_of_range")
    assert bad_meal.status_code == 422
    assert (no_household.status_code, no_household.json()["code"]) == (422, "orders.hub_required")
    assert [i["id"] for i in closed.json()["items"]] == [str(dinner)]
    assert await catalog_api.tiffin_menu(db_session, uuid.uuid4()) is None
