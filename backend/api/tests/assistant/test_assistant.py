"""Ghar Saathi's API side (M13 §3): the ML service gets only scrubbed text and a trimmed week;
its intents become proposal cards here (the line, the catalog product, the default quantity,
which week) or a question back; confirm through M3's facade against the version the card was
made from; the history window; the fallback, the cap and expiry (concurrency: test_races.py)."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant.models import UsageDaily
from gharbasket.platform.models import OutboxEvent

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import GENERATE_AT, NOW, PRIYA, VIKRAM, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

ML_URL = "http://ml.test"
ML_TOKEN = "ml-token"
WEEK_TWO_AT = "2026-10-17T12:30:00Z"  # Sat 18:00 IST → delivery Tue 20 Oct


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


class FakeML:
    """The ML service's /assistant/turn: records what the API sent, answers what the test says."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.answer: dict[str, Any] | None = None

    def transport(self) -> httpx2.MockTransport:
        def handler(request: httpx2.Request) -> httpx2.Response:
            if request.url != f"{ML_URL}/assistant/turn":
                return httpx2.Response(503)  # the planner and the twin fall back to rules
            self.requests.append(json.loads(request.content))
            self.headers.append(dict(request.headers))
            if self.answer is None:
                return httpx2.Response(503, json={"code": "ml.assistant_unavailable"})
            return httpx2.Response(200, json=self.answer)

        return httpx2.MockTransport(handler)

    def says(self, reply: str, intents: list[dict[str, Any]] | None = None, tokens: int = 1200):
        intents = intents or []
        self.answer = {
            "reply": reply,
            "intents": intents,
            "tool_calls": [i["action"] for i in intents] or ["get_my_week"],
            "usage": {"input_tokens": tokens, "output_tokens": 80, "cache_read_tokens": 5000},
            "model": "claude-opus-5-5",
            "stop_reason": "end_turn",
        }


async def generate(api: httpx2.AsyncClient, at: str = GENERATE_AT) -> None:
    answer = await api.post("/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": at})
    assert answer.status_code == 200, answer.text


async def ready_api(
    clients: ClientFactory, google: FakeGoogle, ml: FakeML, **settings: Any
) -> tuple[httpx2.AsyncClient, dict, dict]:
    api = clients(
        http=httpx2.AsyncClient(transport=ml.transport()),
        ml_service_url=ML_URL,
        ml_service_token=ML_TOKEN,
        **settings,
    )
    priya, _ = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    box = (await api.get("/v1/cycles/current", headers=priya)).json()
    return api, priya, box


async def say(
    api: httpx2.AsyncClient, auth: dict, text: str, session_id: str | None = None, lang="en"
) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    if session_id:
        body["session_id"] = session_id
    answer = await api.post(
        "/v1/assistant/messages", json=body, headers={**auth, "Accept-Language": lang}
    )
    assert answer.status_code == 200, answer.text
    return answer.json()


@time_machine.travel(NOW, tick=False)
async def test_only_scrubbed_text_and_a_trimmed_week_leave_the_api(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    ml = FakeML()
    api, priya, box = await ready_api(clients, google, ml)
    ml.says("ठीक है, कार्ड देख लीजिए। Call me on 9876543210.")

    turn = await say(
        api,
        priya,
        "Main Priya Sharma, flat A-1203 Sunrise Towers, Kharghar 410210 se. Mera number "
        "+91 98200 12345, mail priya.sharma@gmail.com, aadhaar 2345 6789 0123. is hafte kya hai?",
        lang="hi",
    )

    sent = ml.requests[0]
    assert set(sent) == {"locale", "messages", "week"} and sent["locale"] == "hi"
    said = sent["messages"][0]["content"]
    secrets = ("Priya", "Sharma", "A-1203", "Sunrise", "410210", "98200", "priya.sharma", "2345")
    for secret in secrets:
        assert secret not in said, secret
    assert "[name]" in said and "[phone]" in said and "[email]" in said
    assert "[id]" in said and "[pin]" in said and "[address]" in said
    # Nothing that identifies the household or the user, and no token to call back with.
    user_id = str((await db_session.execute(select(UsageDaily.user_id))).scalar_one())
    dumped = json.dumps(sent)
    assert user_id not in dumped and box["household_id"] not in dumped and box["id"] not in dumped
    assert "Bearer" not in dumped and "token" not in dumped and "captain" not in dumped
    assert ml.headers[0]["authorization"] == f"Bearer {ML_TOKEN}"
    week = sent["week"]
    assert set(week) == {
        "delivery_date",
        "status",
        "cutoff_at",
        "can_edit",
        "total_paise",
        "lines",
        "meal_plan",
    }
    assert week["delivery_date"] == box["delivery_date"] and week["can_edit"] is True
    assert week["total_paise"] == box["total_paise"]
    assert [line["handle"] for line in week["lines"]] == [
        f"L{i}" for i in range(1, len(box["lines"]) + 1)
    ]
    first, mine = week["lines"][0], box["lines"][0]
    assert (first["slug"], first["qty"], first["line_total_paise"]) == (
        mine["slug"],
        mine["qty"],
        mine["total_paise"],
    )
    assert set(first) == {
        "handle",
        "slug",
        "name",
        "qty",
        "unit",
        "unit_price_paise",
        "line_total_paise",
        "reason_code",
    }
    assert all(set(meal) == {"date", "slot", "dishes"} for meal in week["meal_plan"])

    # Stored scrubbed too, the reply included; the tokens counted are the uncached ones.
    assert "[phone]" in turn["reply"]["content"] and "9876" not in turn["reply"]["content"]
    assert turn["source"] == "ml" and turn["tokens_today"] == 1280
    history = (await api.get(f"/v1/assistant/sessions/{turn['session_id']}", headers=priya)).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert history["messages"][0]["content"] == said and history["model"] == "claude-opus-5-5"


@time_machine.travel(NOW, tick=False)
async def test_intents_become_cards_or_questions_and_cards_confirm_through_m3(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    ml = FakeML()
    api, priya, box = await ready_api(clients, google, ml)
    first = box["lines"][0]
    assert "pineapple" not in {line["slug"] for line in box["lines"]}
    ml.says(
        "Theek hai.",
        [
            {"action": "edit_box_line", "op": "remove", "line": "L1", "product": first["slug"]},
            {"action": "edit_box_line", "op": "add", "product": "Pineapple"},  # qty defaults to 1
            {"action": "edit_box_line", "op": "qty", "line": "L1"},  # no qty: asked back
            {"action": "edit_box_line", "op": "add", "product": "dragonfruit"},  # unknown
            {"action": "edit_box_line", "op": "add", "product": "  "},  # nothing to match
            {"action": "edit_box_line", "op": "remove", "line": "L99", "product": ""},
        ],
    )
    turn = await say(api, priya, "first wala hata do, ek pineapple daal do")

    remove, add = turn["proposals"]
    assert remove["payload"] == {
        "op": "remove",
        "product": first["slug"],
        "product_id": first["product_id"],
        "qty": None,
        "price_change_paise": -first["total_paise"],
        "cycle_id": box["id"],
        "version": box["version"],
        "delivery_date": box["delivery_date"],
    }
    assert remove["summary"] == f"Remove {first['name']} from the box for 13 Oct"
    assert add["payload"]["product"] == "pineapple" and add["payload"]["qty"] == 1
    assert add["summary"].startswith("Add 1 x Pineapple (")
    reply = turn["reply"]["content"]
    assert reply.startswith("Theek hai.")
    assert "How many packs? Any whole number from 1 to 20." in reply
    assert "I couldn't find “dragonfruit”" in reply
    assert "Which item should I add?" in reply
    assert "I couldn't find that item in this week's box." in reply

    # Confirm the removal: M3 applies it at the version the card was made from.
    confirmed = await api.post(f"/v1/assistant/confirm/{remove['id']}", headers={**priya, **key()})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["proposal"]["result"]["version"] == box["version"] + 1
    # The card shown next to it moved to the new version, so it still confirms.
    added = await api.post(f"/v1/assistant/confirm/{add['id']}", headers={**priya, **key()})
    assert added.status_code == 200, added.text
    week = (await api.get("/v1/cycles/current", headers=priya)).json()
    slugs = {line["slug"] for line in week["lines"]}
    assert first["slug"] not in slugs and "pineapple" in slugs
    events = await db_session.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "assistant.proposal_confirmed")
    )
    assert [e.payload["action"] for e in events.scalars()] == ["edit_box_line"] * 2
    again = await api.post(f"/v1/assistant/confirm/{remove['id']}", headers={**priya, **key()})
    assert (again.status_code, again.json()["code"]) == (409, "assistant.proposal_decided")

    # A card made before the week changed in the app is refused by M3's version check.
    second = week["lines"][1]
    ml.says("Ok.", [{"action": "edit_box_line", "op": "qty", "line": "L2", "qty": 5}])
    stale = (await say(api, priya, "doosra 5 kar do"))["proposals"][0]
    assert stale["payload"]["qty"] == 5 and stale["payload"]["product"] == second["slug"]
    changed = await api.patch(
        f"/v1/cycles/{week['id']}/lines",
        json={"ops": [{"op": "qty", "product_id": second["product_id"], "qty": 3}]},
        headers={**priya, "If-Match": f'"{week["version"]}"'},
    )
    assert changed.status_code == 200, changed.text
    refused = await api.post(f"/v1/assistant/confirm/{stale['id']}", headers={**priya, **key()})
    assert (refused.status_code, refused.json()["code"]) == (412, "subscriptions.version_conflict")

    # Reject; another user's proposal does not exist for me.
    ml.says("Ok.", [{"action": "skip_week", "week": "this"}])
    skip = (await say(api, priya, "skip"))["proposals"][0]
    rejected = await api.post(f"/v1/assistant/proposals/{skip['id']}/reject", headers=priya)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    manager, _ = await as_user(api, google, VIKRAM)
    assert (
        await api.post(f"/v1/assistant/confirm/{skip['id']}", headers={**manager, **key()})
    ).status_code == 404
    assert (
        await api.get(f"/v1/assistant/sessions/{uuid.uuid4()}", headers=priya)
    ).status_code == 404


@time_machine.travel(NOW, tick=False)
async def test_agle_hafte_skip_skips_next_week_not_this_one(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()
    api, priya, box = await ready_api(clients, google, ml)
    ml.says("Agle hafte ka card.", [{"action": "skip_week", "week": "next"}])
    early = await say(api, priya, "agle hafte skip kar do, hum Pune ja rahe hain")
    assert early["proposals"] == []
    assert "hasn't been planned yet" in early["reply"]["content"]

    await generate(api, WEEK_TWO_AT)
    turn = await say(api, priya, "agle hafte skip kar do, hum Pune ja rahe hain", lang="hi")
    card = turn["proposals"][0]
    assert card["payload"]["week"] == "next" and card["payload"]["delivery_date"] == "2026-10-20"
    assert card["summary"] == "20 Oct का बॉक्स स्किप करें"
    done = await api.post(f"/v1/assistant/confirm/{card['id']}", headers={**priya, **key()})
    assert done.status_code == 200, done.text
    assert done.json()["proposal"]["result"]["status"] == "skipped"
    this_week = (await api.get("/v1/cycles/current", headers=priya)).json()
    assert this_week["id"] == box["id"] and this_week["status"] == box["status"]

    ml.says("Is hafte ka card.", [{"action": "skip_week", "week": "this"}])
    now_card = (await say(api, priya, "is hafte skip"))["proposals"][0]
    assert now_card["payload"]["cycle_id"] == box["id"]
    assert now_card["payload"]["delivery_date"] == "2026-10-13"


@time_machine.travel(NOW, tick=False)
async def test_the_history_window_starts_with_the_person_and_alternates(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()
    api, priya, _ = await ready_api(clients, google, ml, assistant_daily_tokens=10**6)
    ml.says("Haan.")
    session_id = None
    for n in range(12):
        session_id = (await say(api, priya, f"sawaal {n}", session_id))["session_id"]

    sent = ml.requests[-1]["messages"]
    assert sent[0] == {"role": "user", "content": "sawaal 2"} and len(sent) == 19
    assert [m["role"] for m in sent] == ["user", "assistant"] * 9 + ["user"]
    assert sent[-1]["content"] == "sawaal 11"
    assert all(len(r["messages"]) % 2 == 1 for r in ml.requests)


@time_machine.travel(NOW, tick=False)
async def test_the_fallback_reply_the_daily_cap_and_expiry(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()  # answers 503 until told otherwise
    api, priya, _ = await ready_api(clients, google, ml, assistant_daily_tokens=2000)
    down = await say(api, priya, "hello", lang="mr")
    assert down["source"] == "fallback" and "घर साथी" in down["reply"]["content"]
    assert down["tokens_today"] == 0

    ml.says("A card.", [{"action": "skip_week", "week": "this"}], tokens=1950)  # 2030 > 2000
    first = await say(api, priya, "skip")
    assert first["tokens_today"] == 2030 and first["tokens_cap"] == 2000
    capped = await api.post("/v1/assistant/messages", json={"text": "and?"}, headers=priya)
    assert (capped.status_code, capped.json()["code"]) == (429, "assistant.daily_cap")
    assert capped.headers["Retry-After"] == "3600" and len(ml.requests) == 2

    # Cards nobody decided expire after 15 minutes (the job), and then cannot be confirmed.
    later = (datetime.now(UTC) + timedelta(minutes=16)).isoformat()
    ran = await api.post("/v1/internal/jobs/expire_proposals", json={"scheduled_for": later})
    assert ran.status_code == 200 and ran.json()["summary"] == {"expired": 1}
    stale = await api.post(
        f"/v1/assistant/confirm/{first['proposals'][0]['id']}", headers={**priya, **key()}
    )
    assert stale.json()["code"] == "assistant.proposal_decided"

    # Without a household there is nothing to talk about (the hub manager has none).
    manager, _ = await as_user(api, google, VIKRAM)
    refused = await api.post("/v1/assistant/messages", json={"text": "hi"}, headers=manager)
    assert (refused.status_code, refused.json()["code"]) == (404, "assistant.not_found")
