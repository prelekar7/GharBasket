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
from sqlalchemy import text
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
            extra = sorted(set(body) - {"locale", "messages", "week"})
            if extra:  # FastAPI's validation error for fields a model forbids
                detail = [
                    {
                        "type": "extra_forbidden",
                        "loc": ["body", f],
                        "msg": "Extra inputs",
                        "input": 1,
                    }
                    for f in extra
                ]
                return httpx2.Response(422, json={"detail": detail})
            return httpx2.Response(200, json=self.answer)

        return httpx2.MockTransport(handler)


class Drifted(FakeML):
    """A new ML service that answers 422 for another reason (here: a status it finds too long),
    alone or together with a forbidden field."""

    def __init__(self, *, also_forbids_today: bool) -> None:
        super().__init__()
        self.also_forbids_today = also_forbids_today

    def transport(self) -> httpx2.MockTransport:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url != f"{ML_URL}/assistant/turn":
                return httpx2.Response(503)
            self.requests.append(json.loads(request.content))
            detail = [
                {
                    "type": "string_too_long",
                    "loc": ["body", "orders", 0, "status"],
                    "msg": "too long",
                    "input": "a-very-long-status-value",
                }
            ]
            if self.also_forbids_today:
                detail.append({"type": "extra_forbidden", "loc": ["body", "today"], "msg": "x"})
            return httpx2.Response(422, json={"detail": detail})

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
async def test_a_database_error_inside_a_part_rolls_back_to_its_savepoint(
    clients: ClientFactory, google: FakeGoogle, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real error from the database (not a Python exception) would abort the transaction; the
    part's savepoint contains it, so the turn proceeds and the session serves the next turn."""
    ml = FakeML()
    api, priya, _ = await ready_api(clients, google, ml)
    ml.says("Theek hai.")
    real = orders_api.tiffin_choices

    async def divides_by_zero(session: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        await session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(orders_api, "tiffin_choices", divides_by_zero)
    first = await say(api, priya, "tiffin?")
    monkeypatch.setattr(orders_api, "tiffin_choices", real)
    second = await say(api, priya, "tiffin?", first["session_id"])

    assert "tiffin" not in ml.requests[0] and "orders" in ml.requests[0]
    assert ml.requests[1]["tiffin"] == {"options": []}  # the next turn sees its menus again
    assert first["source"] == second["source"] == "ml"
    history = (await api.get(f"/v1/assistant/sessions/{first['session_id']}", headers=priya)).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"] * 2


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


@pytest.mark.parametrize("also_forbids_today", [False, True])
@time_machine.travel(NOW, tick=False)
async def test_a_422_of_another_meaning_is_not_taken_for_an_old_ml_service(
    clients: ClientFactory, google: FakeGoogle, also_forbids_today: bool
) -> None:
    ml = Drifted(also_forbids_today=also_forbids_today)
    api, priya, _ = await ready_api(clients, google, ml)

    turn = await say(api, priya, "tiffin?")

    assert len(ml.requests) == 1  # not asked again without the new fields
    assert turn["source"] == "fallback" and {"today", "orders", "tiffin"} <= set(ml.requests[0])


@time_machine.travel(NOW, tick=False)
async def test_an_intent_the_api_does_not_know_is_ignored(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()
    api, priya, _ = await ready_api(clients, google, ml)
    ml.says("Done!", [{"action": "refund_order", "order": "O1"}, {"action": 7}])

    turn = await say(api, priya, "refund my order")

    assert turn["proposals"] == [] and turn["reply"]["content"] == "Done!"
