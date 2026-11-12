"""What the API adds for `track_order` and `get_tiffin_menus` (M13 §3.1): the IST date, the
household's current orders and the bookable tiffin menus, all with opaque handles and no ids,
kitchens or names; a part that fails is left out; an ML service from before this change is asked
the old way; an intent the API does not know is ignored."""

import json
import uuid
from datetime import date
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.test_tiffin_orders import book, tiffin_slot
from ..orders.tiffin_menus import menu
from ..subscriptions.test_cycles import NOW
from .test_assistant import ML_URL, FakeML, ready_api, say

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

MONDAY = date(2026, 10, 12)  # the first day with menus; its cut-off is Sun 21:00 IST


class OldML(FakeML):
    """An ML service from before M13 §3.1: it refuses the fields it does not know (422)."""

    def transport(self) -> httpx2.MockTransport:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url != f"{ML_URL}/assistant/turn":
                return httpx2.Response(503)
            body = json.loads(request.content)
            self.requests.append(body)
            if set(body) - {"locale", "messages", "week"}:
                return httpx2.Response(422, json={"detail": "extra fields not permitted"})
            return httpx2.Response(200, json=self.answer)

        return httpx2.MockTransport(handler)


@time_machine.travel(NOW, tick=False)
async def test_orders_and_menus_go_out_with_handles_and_no_ids(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await tiffin_slot(db_session)
    cook = uuid.uuid4()
    lunch = await menu(db_session, MONDAY, partner_id=cook, price_paise=11_000)
    dinner = await menu(db_session, MONDAY, meal="dinner", partner_id=cook, price_paise=9_500)
    await db_session.commit()
    ml = FakeML()
    api, priya, box = await ready_api(clients, google, ml)
    booked = await book(api, priya, menu_id=str(lunch.id), portions=2)
    assert booked.status_code == 201, booked.text
    ml.says("Dekh liya.")

    await say(api, priya, "mera tiffin kahan hai?", lang="hi")

    sent = ml.requests[0]
    assert set(sent) == {"locale", "messages", "week", "today", "orders", "tiffin"}
    assert sent["today"] == "2026-10-10"
    [order] = sent["orders"]
    assert order == {
        "handle": "O1",
        "kind": "tiffin",
        "status": "approved",
        "delivery_date": "2026-10-12",
        "meal": "lunch",
        "window": {"start": "12:30", "end": "13:30"},
        "eta": None,
        "on_the_way": False,
    }
    first, second = sent["tiffin"]["options"]
    assert (first["date"], first["meal"], first["booked"], first["menus"]) == (
        "2026-10-12",
        "lunch",
        True,
        [],
    )  # booked: no menus to pick
    assert (second["meal"], second["booked"], second["order_by"]) == (
        "dinner",
        False,
        "2026-10-11T15:30:00+00:00",
    )
    assert second["menus"][0]["handle"] == "T1" and second["menus"][0]["price_paise"] == 9_500
    assert set(second["menus"][0]) == {"handle", "dishes", "price_paise"}
    assert second["menus"][0]["dishes"] and all(
        isinstance(d, str) for d in second["menus"][0]["dishes"]
    )

    # No order, menu, partner, hub, society or household id anywhere in what left the API.
    dumped = json.dumps(sent)
    ids = (
        booked.json()["id"],
        str(lunch.id),
        str(dinner.id),
        str(cook),
        str(SEED_HUB_ID),
        str(SEED_SOCIETY_ID),
        box["household_id"],
    )
    for secret in ids:
        assert secret not in dumped, secret
    assert "kitchen" not in dumped and "partner" not in dumped and "captain" not in dumped


@time_machine.travel(NOW, tick=False)
async def test_a_part_that_fails_is_left_out_and_the_turn_goes_on(
    clients: ClientFactory,
    google: FakeGoogle,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ml = FakeML()
    api, priya, _ = await ready_api(clients, google, ml)
    ml.says("Theek hai.")

    async def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("M7 is down")

    monkeypatch.setattr(orders_api, "tiffin_choices", broken)
    first = await say(api, priya, "tiffin?")
    monkeypatch.setattr(logistics_api, "delivery_signals", broken)
    second = await say(api, priya, "order?")

    assert "tiffin" not in ml.requests[0] and ml.requests[0]["orders"] == []
    assert {"today"} <= set(ml.requests[1]) and not {"orders", "tiffin"} & set(ml.requests[1])
    assert first["source"] == second["source"] == "ml"


@time_machine.travel(NOW, tick=False)
async def test_an_old_ml_service_is_asked_again_the_old_way(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = OldML()
    api, priya, _ = await ready_api(clients, google, ml)
    ml.says("Namaste.")

    turn = await say(api, priya, "is hafte kya aa raha hai?")

    assert turn["source"] == "ml" and turn["reply"]["content"] == "Namaste."
    new, old = ml.requests
    assert {"today", "orders", "tiffin"} <= set(new) and set(old) == {"locale", "messages", "week"}
    assert old["messages"] == new["messages"] and old["week"] == new["week"]


@time_machine.travel(NOW, tick=False)
async def test_an_intent_the_api_does_not_know_is_ignored(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()
    api, priya, _ = await ready_api(clients, google, ml)
    ml.says("Done!", [{"action": "refund_order", "order": "O1"}, {"action": 7}])

    turn = await say(api, priya, "refund my order")

    assert turn["proposals"] == [] and turn["reply"]["content"] == "Done!"
