"""Business logic of M13 Assistant (Ghar Saathi, spec §3): sessions, the turn through the ML
service with the user's token, proposals confirmed through the owning module's facade, the
daily token cap and the scrub of personal data before anything is stored.
"""

import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant import events
from gharbasket.modules.assistant.models import Message, Proposal, Session, UsageDaily
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.assistant")
IST = ZoneInfo("Asia/Kolkata")
PROPOSAL_TTL = timedelta(minutes=15)
HISTORY = 20  # messages sent to the model
PHONE = re.compile(r"(\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}")
FALLBACK_REPLY = {
    "hi": "माफ़ कीजिए, घर साथी अभी उपलब्ध नहीं है। अपना हफ़्ता ऐप के Week टैब में देखें।",
    "mr": "माफ करा, घर साथी सध्या उपलब्ध नाही. तुमचा आठवडा ॲपच्या Week टॅबमध्ये पाहा.",
    "en": "Sorry, Ghar Saathi is not available right now. Your week is in the app's Week tab.",
}


@asynccontextmanager
async def http_client(given: httpx2.AsyncClient | None) -> AsyncIterator[httpx2.AsyncClient]:
    """The app's shared client, or a short-lived one (jobs and tests without a lifespan)."""
    if given is not None:
        yield given
        return
    async with httpx2.AsyncClient() as client:
        yield client


def today_ist() -> date:
    return datetime.now(IST).date()


def now() -> datetime:
    return datetime.now(UTC)


def scrub(text: str) -> str:
    return PHONE.sub("[phone]", text)


def not_found(what: str = "assistant.not_found", detail: str = "No such session.") -> ProblemError:
    return ProblemError(404, what, detail)


async def get_session(session: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID) -> Session:
    found = await session.get(Session, session_id)
    if found is None or found.user_id != user_id:
        raise not_found()
    return found


async def start_session(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID, locale: str
) -> Session:
    chat = Session(user_id=user_id, household_id=household_id, locale=locale)
    session.add(chat)
    await session.flush()
    await session.refresh(chat)
    return chat


async def messages_of(session: AsyncSession, session_id: uuid.UUID) -> list[Message]:
    rows = await session.execute(
        select(Message).where(Message.session_id == session_id).order_by(Message.id)
    )
    return list(rows.scalars())


async def proposals_of(session: AsyncSession, session_id: uuid.UUID) -> list[Proposal]:
    rows = await session.execute(
        select(Proposal).where(Proposal.session_id == session_id).order_by(Proposal.id)
    )
    return list(rows.scalars())


async def usage_today(session: AsyncSession, user_id: uuid.UUID) -> UsageDaily:
    row = await session.get(UsageDaily, (user_id, today_ist()))
    if row is None:
        row = UsageDaily(user_id=user_id, day=today_ist(), tokens_in=0, tokens_out=0, turns=0)
        session.add(row)
        await session.flush()
    return row


def check_cap(usage: UsageDaily, cap: int) -> None:
    if usage.tokens_in + usage.tokens_out >= cap:
        raise ProblemError(
            429,
            "assistant.daily_cap",
            "Ghar Saathi has done enough thinking for today; the app's Week tab has everything.",
            headers={"Retry-After": "3600"},
        )


async def turn(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    chat: Session,
    *,
    text: str,
    user_token: str,
    api_base_url: str,
) -> tuple[Message, list[Proposal], str, UsageDaily]:
    """Store the user's message, ask the ML service for the reply (with the user's token so the
    tools act as them), store the reply and the proposals. Returns (reply, proposals, source)."""
    usage = await usage_today(session, chat.user_id)
    check_cap(usage, settings.assistant_daily_tokens)
    asked = Message(session_id=chat.id, role="user", content=scrub(text))
    session.add(asked)
    await session.flush()
    history = (await messages_of(session, chat.id))[-HISTORY:]
    body = {
        "session_id": str(chat.id),
        "locale": chat.locale,
        "messages": [{"role": m.role, "content": m.content} for m in history],
        "api_base_url": api_base_url,
        "user_token": user_token,
    }
    answer = await _ask(http, settings, body)
    source = "ml"
    if answer is None:
        source = "fallback"
        answer = {
            "reply": FALLBACK_REPLY.get(chat.locale[:2], FALLBACK_REPLY["en"]),
            "proposals": [],
            "tool_calls": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "model": None,
        }
    reply = Message(
        session_id=chat.id,
        role="assistant",
        content=scrub(str(answer["reply"])),
        tool_calls=list(answer.get("tool_calls", [])),
        tokens_in=int(answer["usage"].get("input_tokens", 0)),
        tokens_out=int(answer["usage"].get("output_tokens", 0)),
    )
    session.add(reply)
    proposals = [
        Proposal(
            session_id=chat.id,
            user_id=chat.user_id,
            household_id=chat.household_id,
            action=p["action"],
            payload=p["payload"],
            summary=str(p["summary"])[:200],
            expires_at=now() + PROPOSAL_TTL,
        )
        for p in answer.get("proposals", [])
    ]
    session.add_all(proposals)
    usage.tokens_in += reply.tokens_in
    usage.tokens_out += reply.tokens_out
    usage.turns += 1
    chat.last_message_at = now()
    if answer.get("model"):
        chat.model = str(answer["model"])
    await session.flush()
    for row in (reply, *proposals):
        await session.refresh(row)
    return reply, proposals, source, usage


async def _ask(
    http: httpx2.AsyncClient, settings: Settings, body: dict[str, Any]
) -> dict[str, Any] | None:
    if not settings.ml_service_url:
        return None
    headers = {}
    if settings.ml_service_token is not None:
        headers["Authorization"] = f"Bearer {settings.ml_service_token.get_secret_value()}"
    try:
        response = await http.post(
            f"{settings.ml_service_url.rstrip('/')}/assistant/turn",
            json=body,
            headers=headers,
            timeout=settings.assistant_timeout_s,
        )
        response.raise_for_status()
        return response.json()
    except (httpx2.HTTPError, ValueError) as exc:
        log.warning("assistant_fallback", reason=type(exc).__name__)
        return None


async def get_proposal(
    session: AsyncSession, user_id: uuid.UUID, proposal_id: uuid.UUID
) -> Proposal:
    found = await session.get(Proposal, proposal_id)
    if found is None or found.user_id != user_id:
        raise not_found("assistant.proposal_not_found", "No such proposal.")
    return found


def _decided(proposal: Proposal) -> None:
    if proposal.status != "pending":
        raise ProblemError(
            409, "assistant.proposal_decided", f"This proposal is already {proposal.status}."
        )
    if proposal.expires_at <= now():
        proposal.status = "expired"
        raise ProblemError(
            409, "assistant.proposal_expired", "This proposal has expired; ask again."
        )


async def confirm(
    session: AsyncSession, proposal: Proposal, *, actor_user_id: uuid.UUID
) -> Proposal:
    """Execute the proposed change through M3's facade, as the user would in the app."""
    _decided(proposal)
    cycle = await subscriptions_api.current_cycle_of(session, proposal.household_id)
    if cycle is None:
        raise ProblemError(409, "assistant.no_cycle", "There is no week to change right now.")
    if proposal.action == "skip_week":
        result = await subscriptions_api.skip_cycle(session, proposal.household_id, cycle.id)
    else:
        result = await subscriptions_api.edit_cycle_lines(
            session, proposal.household_id, cycle.id, [await _line_edit(session, proposal.payload)]
        )
    proposal.status = "confirmed"
    proposal.decided_at = now()
    proposal.result = result
    await session.flush()
    await publish(
        session,
        events.PROPOSAL_CONFIRMED,
        {
            "proposal_id": str(proposal.id),
            "household_id": str(proposal.household_id),
            "user_id": str(actor_user_id),
            "action": proposal.action,
        },
    )
    return proposal


async def _line_edit(session: AsyncSession, payload: dict[str, Any]) -> subscriptions_api.LineEdit:
    """The model names products by slug (or by what the person typed, for an add)."""
    slug = str(payload.get("product", "")).strip().lower().replace(" ", "-")
    product_id = (await catalog_api.product_ids_by_slug(session, [slug])).get(slug)
    if product_id is None:
        raise ProblemError(
            422, "assistant.product_unknown", f"We do not know a product called {slug!r}."
        )
    qty = payload.get("qty")
    return subscriptions_api.LineEdit(
        str(payload.get("op")), product_id, qty=int(qty) if qty is not None else None
    )


async def reject(session: AsyncSession, proposal: Proposal) -> Proposal:
    _decided(proposal)
    proposal.status = "rejected"
    proposal.decided_at = now()
    await session.flush()
    return proposal


async def expire_pending(session: AsyncSession, at: datetime) -> int:
    rows = await session.execute(
        select(Proposal).where(Proposal.status == "pending", Proposal.expires_at <= at)
    )
    expired = list(rows.scalars())
    for proposal in expired:
        proposal.status = "expired"
        proposal.decided_at = at
    await session.flush()
    return len(expired)
