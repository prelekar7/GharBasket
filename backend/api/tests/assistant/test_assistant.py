"""Ghar Saathi's API side (M13 §3): a turn through the ML service as the user, proposal cards,
confirm through M3's facade, reject, the expiry job, the fallback and the daily cap."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.models import OutboxEvent

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import GENERATE_AT, NOW, PRIYA, VIKRAM, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

ML_URL = "http://ml.test"
ML_TOKEN = "ml-token"


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

    def says(self, reply: str, proposals: list[dict[str, Any]], tokens: int = 1200) -> None:
        self.answer = {
            "reply": reply,
            "proposals": proposals,
            "tool_calls": [p["action"] for p in proposals] or ["get_my_week"],
            "usage": {"input_tokens": tokens, "output_tokens": 80},
            "model": "claude-opus-5-5",
            "stop_reason": "end_turn",
        }


async def generate(api: httpx2.AsyncClient) -> None:
    answer = await api.post(
        "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT}
    )
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


@time_machine.travel(NOW, tick=False)
async def test_a_turn_then_confirm_and_reject_the_cards(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    ml = FakeML()
    api, priya, box = await ready_api(clients, google, ml)
    first = box["lines"][0]
    ml.says(
        "ठीक है, दो कार्ड confirm कर दीजिए। Call me on 9876543210.",
        [
            {
                "action": "edit_box_line",
                "payload": {"op": "remove", "product": first["slug"], "qty": None},
                "summary": f"Remove {first['name']} from this week's box",
            },
            {"action": "skip_week", "payload": {}, "summary": "Skip this week's box"},
        ],
    )

    said = await api.post(
        "/v1/assistant/messages",
        json={"text": f"is hafte {first['name']} mat bhejna, mera number 9876543210"},
        headers={**priya, "Accept-Language": "hi"},
    )
    assert said.status_code == 200, said.text
    turn = said.json()
    assert turn["source"] == "ml" and turn["tokens_today"] == 1280
    assert [p["action"] for p in turn["proposals"]] == ["edit_box_line", "skip_week"]
    assert "[phone]" in turn["reply"]["content"] and "9876" not in turn["reply"]["content"]
    # The ML service got the scrubbed history, the user's own token and where to call back.
    sent = ml.requests[0]
    assert sent["locale"] == "hi" and sent["api_base_url"] == "https://t"
    assert sent["user_token"] == priya["Authorization"].removeprefix("Bearer ")
    assert "9876" not in sent["messages"][0]["content"]
    assert ml.headers[0]["authorization"] == f"Bearer {ML_TOKEN}"

    # The session shows both messages (scrubbed) and the pending cards.
    history = (await api.get(f"/v1/assistant/sessions/{turn['session_id']}", headers=priya)).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    assert "[phone]" in history["messages"][0]["content"]
    assert history["model"] == "claude-opus-5-5"
    assert all(p["status"] == "pending" for p in history["proposals"])
    edit, skip = turn["proposals"]

    # Confirm the edit: the line is gone from the week, exactly as the app's PATCH would do it.
    confirmed = await api.post(f"/v1/assistant/confirm/{edit['id']}", headers={**priya, **key()})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["proposal"]["status"] == "confirmed"
    assert confirmed.json()["proposal"]["result"]["version"] == 2
    week = (await api.get("/v1/cycles/current", headers=priya)).json()
    assert first["slug"] not in {line["slug"] for line in week["lines"]}
    events = await db_session.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "assistant.proposal_confirmed")
    )
    assert [e.payload["action"] for e in events.scalars()] == ["edit_box_line"]
    again = await api.post(f"/v1/assistant/confirm/{edit['id']}", headers={**priya, **key()})
    assert (again.status_code, again.json()["code"]) == (409, "assistant.proposal_decided")

    # Reject the skip: nothing changes; it cannot be confirmed afterwards.
    rejected = await api.post(f"/v1/assistant/proposals/{skip['id']}/reject", headers=priya)
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert (await api.get("/v1/cycles/current", headers=priya)).json()["status"] == week["status"]
    late = await api.post(f"/v1/assistant/confirm/{skip['id']}", headers={**priya, **key()})
    assert late.json()["code"] == "assistant.proposal_decided"

    # Another user's session or proposal does not exist for me.
    assert (
        await api.get(f"/v1/assistant/sessions/{uuid.uuid4()}", headers=priya)
    ).status_code == 404
    manager, _ = await as_user(api, google, VIKRAM)
    assert (
        await api.post(f"/v1/assistant/confirm/{edit['id']}", headers={**manager, **key()})
    ).status_code == 404


@time_machine.travel(NOW, tick=False)
async def test_expiry_unknown_products_and_the_skip_card(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()
    api, priya, _ = await ready_api(clients, google, ml)
    ml.says(
        "Two cards.",
        [
            {
                "action": "edit_box_line",
                "payload": {"op": "add", "product": "dragonfruit", "qty": 1},
                "summary": "Add 1 x dragonfruit to this week's box",
            },
            {"action": "skip_week", "payload": {}, "summary": "Skip this week's box"},
        ],
    )
    turn = (
        await api.post("/v1/assistant/messages", json={"text": "add dragonfruit"}, headers=priya)
    ).json()
    add, skip = turn["proposals"]
    unknown = await api.post(f"/v1/assistant/confirm/{add['id']}", headers={**priya, **key()})
    assert (unknown.status_code, unknown.json()["code"]) == (422, "assistant.product_unknown")

    # A second turn in the same session carries the history; the skip card gets confirmed.
    ml.says("Done.", [])
    second = await api.post(
        "/v1/assistant/messages",
        json={"session_id": turn["session_id"], "text": "haan skip kar do"},
        headers=priya,
    )
    assert second.status_code == 200 and len(ml.requests[1]["messages"]) == 3
    done = await api.post(f"/v1/assistant/confirm/{skip['id']}", headers={**priya, **key()})
    assert done.status_code == 200 and done.json()["proposal"]["result"]["status"] == "skipped"

    # Cards nobody decided expire after 15 minutes (the job), and then cannot be confirmed.
    ml.says("A card.", [{"action": "skip_week", "payload": {}, "summary": "Skip"}])
    third = (await api.post("/v1/assistant/messages", json={"text": "skip"}, headers=priya)).json()
    later = (datetime.now(UTC) + timedelta(minutes=16)).isoformat()
    ran = await api.post("/v1/internal/jobs/expire_proposals", json={"scheduled_for": later})
    # Two: this card and the unknown-product card that was never decided.
    assert ran.status_code == 200 and ran.json()["summary"] == {"expired": 2}
    stale = await api.post(
        f"/v1/assistant/confirm/{third['proposals'][0]['id']}", headers={**priya, **key()}
    )
    assert stale.json()["code"] == "assistant.proposal_decided"


@time_machine.travel(NOW, tick=False)
async def test_the_fallback_reply_and_the_daily_cap(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    ml = FakeML()  # answers 503 until told otherwise
    api, priya, _ = await ready_api(clients, google, ml, assistant_daily_tokens=2000)
    down = await api.post(
        "/v1/assistant/messages", json={"text": "hello"}, headers={**priya, "Accept-Language": "mr"}
    )
    assert down.status_code == 200 and down.json()["source"] == "fallback"
    assert "घर साथी" in down.json()["reply"]["content"] and down.json()["tokens_today"] == 0

    ml.says("Namaste!", [], tokens=1950)  # 2030 with the output: over the cap
    first = await api.post("/v1/assistant/messages", json={"text": "hi"}, headers=priya)
    assert first.json()["tokens_today"] == 2030 and first.json()["tokens_cap"] == 2000
    capped = await api.post("/v1/assistant/messages", json={"text": "and?"}, headers=priya)
    assert (capped.status_code, capped.json()["code"]) == (429, "assistant.daily_cap")
    assert capped.headers["Retry-After"] == "3600" and len(ml.requests) == 2

    # Without a household there is nothing to talk about (the hub manager has none).
    manager, _ = await as_user(api, google, VIKRAM)
    refused = await api.post("/v1/assistant/messages", json={"text": "hi"}, headers=manager)
    assert (refused.status_code, refused.json()["code"]) == (404, "assistant.not_found")
