"""What M13's `track_order` and `book_tiffin` read and call in M4 (M13 §3.1): the household's
current orders, the tiffin menus it may still book (by date and meal, `booked` where it has one),
and the booking function behind `POST /v1/orders`."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import Order
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_orders import PRIYA, SUNITA, as_user
from .test_tiffin_orders import DAY, NOW, book, key, tiffin_slot
from .tiffin_menus import menu

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]


async def household_of(db: AsyncSession, user_id: uuid.UUID) -> identity_api.HouseholdRef:
    found = await identity_api.household_for_user(db, user_id)
    assert found is not None and found.hub_id is not None
    return found


async def choices_of(
    db: AsyncSession, household: identity_api.HouseholdRef, lang: Language = "en"
) -> list[orders_api.TiffinChoice]:
    assert household.hub_id is not None
    return await orders_api.tiffin_choices(db, household.id, household.hub_id, lang)


@time_machine.travel(NOW, tick=False)
async def test_the_choices_are_the_open_menus_by_date_and_meal(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await tiffin_slot(db_session)
    a = (await menu(db_session, DAY, price_paise=11_000)).id
    b = (await menu(db_session, DAY, price_paise=13_000)).id
    await menu(db_session, DAY, meal="dinner", status="submitted")  # not approved
    await menu(db_session, DAY, hub_id=uuid.uuid4())  # another hub's
    await menu(db_session, DAY + timedelta(days=8))  # beyond tomorrow + 6
    await db_session.commit()
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    _, sunita_id = await as_user(api, google, SUNITA)
    household, other = (
        await household_of(db_session, priya_id),
        await household_of(db_session, sunita_id),
    )

    found = await choices_of(db_session, household, "hi")
    [lunch] = found
    assert (lunch.on_date, lunch.meal, lunch.booked) == (DAY, "lunch", False)
    assert lunch.order_by == datetime(2026, 10, 11, 15, 30, tzinfo=UTC)
    assert {m.id for m in lunch.menus} == {a, b} and {m.price_paise for m in lunch.menus} == {
        11_000,
        13_000,
    }
    assert all(len(m.dishes) == 2 and all(m.dishes) for m in lunch.menus)  # names, no ids

    # Priya's own tiffin for the meal: listed as booked, with no menus; Sunita still sees both.
    assert (await book(api, priya, menu_id=str(a))).status_code == 201
    [mine] = await choices_of(db_session, household, "en")
    [theirs] = await choices_of(db_session, other, "en")
    assert (mine.booked, mine.menus) == (True, ()) and len(theirs.menus) == 2

    # Cancelled, it is open again; once its order_by has passed it is gone.
    placed = await orders_api.household_orders(db_session, household.id, date(2026, 10, 10))
    cancelled = await api.post(f"/v1/orders/{placed[0].id}/cancel", headers={**priya, **key()})
    assert cancelled.status_code == 200, cancelled.text
    [again] = await choices_of(db_session, household, "en")
    assert again.booked is False and len(again.menus) == 2
    with time_machine.travel(datetime(2026, 10, 11, 15, 30, tzinfo=UTC), tick=False):
        assert await choices_of(db_session, household, "en") == []


@time_machine.travel(NOW, tick=False)
async def test_the_households_current_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await tiffin_slot(db_session)
    lunch = (await menu(db_session, DAY)).id
    await db_session.commit()
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    _, sunita_id = await as_user(api, google, SUNITA)
    household, other = (
        await household_of(db_session, priya_id),
        await household_of(db_session, sunita_id),
    )
    today = date(2026, 10, 10)

    assert await orders_api.household_orders(db_session, household.id, today) == []
    made = await book(api, priya, menu_id=str(lunch), portions=2)
    [order] = await orders_api.household_orders(db_session, household.id, today)
    assert str(order.id) == made.json()["id"] and (order.kind, order.status) == (
        "tiffin",
        "approved",
    )
    assert await orders_api.household_orders(db_session, other.id, today) == []  # not hers

    # A delivered order of an earlier day is no longer current; one that is not final still is.
    order.status = "delivered"
    order.delivery_date = today - timedelta(days=1)
    await db_session.flush()
    assert await orders_api.household_orders(db_session, household.id, today) == []
    order.status = "redelivery"
    await db_session.flush()
    assert [o.id for o in await orders_api.household_orders(db_session, household.id, today)] == [
        order.id
    ]
    order.delivery_date = today - timedelta(days=30)  # stale
    await db_session.flush()
    assert await orders_api.household_orders(db_session, household.id, today) == []
    order.status = "cancelled"
    order.delivery_date = DAY
    await db_session.flush()
    assert await orders_api.household_orders(db_session, household.id, today) == []


@time_machine.travel(NOW, tick=False)
async def test_book_tiffin_is_the_function_behind_post_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await tiffin_slot(db_session)
    lunch = (await menu(db_session, DAY, price_paise=12_000)).id
    await db_session.commit()
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    household = await household_of(db_session, priya_id)
    profile = await identity_api.household_plan_profile(db_session, household.id)
    assert profile is not None

    order = await orders_api.book_tiffin(db_session, priya_id, lunch)  # default portions
    assert isinstance(order, Order) and (order.kind, order.status) == ("tiffin", "approved")
    assert order.portions == min(profile.size, 12)
    assert order.total_paise == min(profile.size, 12) * 12_000
    with pytest.raises(ProblemError) as twice:
        await orders_api.book_tiffin(db_session, priya_id, lunch, 1)
    assert (twice.value.status, twice.value.code) == (409, "orders.tiffin_already_booked")
    with pytest.raises(ProblemError) as unknown:
        await orders_api.book_tiffin(db_session, priya_id, uuid.uuid4())
    assert unknown.value.code == "orders.menu_unknown"
