"""Business logic of M13 Assistant (Ghar Saathi, spec §3): sessions, a turn through the ML
service, the intents it returns resolved into proposal cards, confirmation through M3's facade,
the daily token cap, and the scrub of personal data before anything is stored or sent.

What leaves the API for the ML service (hosted outside India, ADR-0025) is only the scrubbed
conversation and a trimmed week (§3): no household or user id, name, address, captain or token.
The ML service answers from that week and calls nothing back; what it wants changed comes back
as intents, which are resolved here (the line, the catalog product, the default quantity, which
week) into proposals the person confirms. Quantities and prices are the API's, never the model's.

A turn runs in three steps so no transaction stays open while the model thinks: `prepare_turn`
(reserve the day's usage row under a lock, store the scrubbed message, build the request;
the router commits), `ask` (the ML call), `finish_turn` (store the reply and the proposals, add
the tokens with SQL increments; the router commits).
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import structlog
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant import events
from gharbasket.modules.assistant.models import Message, Proposal, Session, UsageDaily
from gharbasket.modules.assistant.scrub import scrub
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.assistant")
IST = ZoneInfo("Asia/Kolkata")
PROPOSAL_TTL = timedelta(minutes=15)
HISTORY = 20  # messages sent to the model
MAX_CONTENT = 4000  # characters a message may carry to the ML service
MAX_INTENTS = 10  # changes a turn may ask for (cards or questions back)
FALLBACK_REPLY = {
    "hi": "माफ़ कीजिए, घर साथी अभी उपलब्ध नहीं है। अपना हफ़्ता ऐप के Week टैब में देखें।",
    "mr": "माफ करा, घर साथी सध्या उपलब्ध नाही. तुमचा आठवडा ॲपच्या Week टॅबमध्ये पाहा.",
    "en": "Sorry, Ghar Saathi is not available right now. Your week is in the app's Week tab.",
}
# What the API says when it cannot turn an intent into a card (appended to the model's reply).
CLARIFY: dict[str, dict[str, str]] = {
    "en": {
        "not_editable": "This week's box can no longer be changed here.",
        "not_in_box": "I couldn't find that item in this week's box.",
        "how_many": "How many packs? Any whole number from 1 to {max}.",
        "add_what": "Which item should I add?",
        "unknown": "I couldn't find “{product}” among what your hub sells this week.",
        "which": "Which one did you mean: {options}?",
        "no_week": "That week's box hasn't been planned yet, so it can't be skipped from here.",
    },
    "hi": {
        "not_editable": "इस हफ़्ते का बॉक्स अब यहाँ से बदला नहीं जा सकता।",
        "not_in_box": "यह चीज़ इस हफ़्ते के बॉक्स में नहीं मिली।",
        "how_many": "कितने पैकेट? 1 से {max} तक कोई भी संख्या।",
        "add_what": "कौन-सी चीज़ जोड़ूँ?",
        "unknown": "“{product}” इस हफ़्ते आपके हब में नहीं मिला।",
        "which": "आपका मतलब कौन-सा था: {options}?",
        "no_week": "उस हफ़्ते का बॉक्स अभी बना नहीं है, इसलिए यहाँ से स्किप नहीं हो सकता।",
    },
    "mr": {
        "not_editable": "या आठवड्याचा बॉक्स आता इथून बदलता येणार नाही.",
        "not_in_box": "ही वस्तू या आठवड्याच्या बॉक्समध्ये सापडली नाही.",
        "how_many": "किती पॅकेट? 1 ते {max} मधली कोणतीही संख्या.",
        "add_what": "कोणती वस्तू जोडू?",
        "unknown": "“{product}” या आठवड्यात तुमच्या हबमध्ये मिळाले नाही.",
        "which": "तुम्हाला कोणते म्हणायचे आहे: {options}?",
        "no_week": "त्या आठवड्याचा बॉक्स अजून तयार नाही, त्यामुळे इथून स्किप करता येणार नाही.",
    },
}
SUMMARY: dict[str, dict[str, str]] = {
    "en": {
        "remove": "Remove {name} from the box for {date}",
        "add": "Add {qty} x {name} ({unit}) to the box for {date}",
        "qty": "Change {name} to {qty} x {unit} in the box for {date}",
        "skip": "Skip the box for {date}",
    },
    "hi": {
        "remove": "{date} के बॉक्स से {name} हटाएँ",
        "add": "{date} के बॉक्स में {qty} x {name} ({unit}) जोड़ें",
        "qty": "{date} के बॉक्स में {name} {qty} x {unit} करें",
        "skip": "{date} का बॉक्स स्किप करें",
    },
    "mr": {
        "remove": "{date} च्या बॉक्समधून {name} काढा",
        "add": "{date} च्या बॉक्समध्ये {qty} x {name} ({unit}) जोडा",
        "qty": "{date} च्या बॉक्समध्ये {name} {qty} x {unit} करा",
        "skip": "{date} चा बॉक्स स्किप करा",
    },
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


# --- the daily cap ------------------------------------------------------------------------------


def check_cap(usage: UsageDaily, cap: int) -> None:
    if usage.tokens_in + usage.tokens_out >= cap:
        raise ProblemError(
            429,
            "assistant.daily_cap",
            "Ghar Saathi has done enough thinking for today; the app's Week tab has everything.",
            headers={"Retry-After": "3600"},
        )


async def reserve_turn(session: AsyncSession, user_id: uuid.UUID, day: date, cap: int) -> None:
    """Count a turn against today's cap. The day's row is created race-free (two first turns of
    the day at once both succeed) and locked while it is checked; the lock ends with the
    transaction, before the model is asked."""
    await session.execute(
        insert(UsageDaily)
        .values(user_id=user_id, day=day, tokens_in=0, tokens_out=0, turns=0)
        .on_conflict_do_nothing(index_elements=["user_id", "day"])
    )
    usage = (
        await session.execute(
            select(UsageDaily)
            .where(UsageDaily.user_id == user_id, UsageDaily.day == day)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    check_cap(usage, cap)
    await session.execute(
        update(UsageDaily)
        .where(UsageDaily.user_id == user_id, UsageDaily.day == day)
        .values(turns=UsageDaily.turns + 1)
    )


async def record_tokens(
    session: AsyncSession, user_id: uuid.UUID, day: date, tokens_in: int, tokens_out: int
) -> int:
    """Add a turn's tokens with SQL increments (no lost update); returns the day's total."""
    total = await session.execute(
        update(UsageDaily)
        .where(UsageDaily.user_id == user_id, UsageDaily.day == day)
        .values(
            tokens_in=UsageDaily.tokens_in + tokens_in,
            tokens_out=UsageDaily.tokens_out + tokens_out,
        )
        .returning(UsageDaily.tokens_in + UsageDaily.tokens_out)
    )
    return int(total.scalar_one())


# --- a turn -------------------------------------------------------------------------------------


def window(messages: list[Message]) -> list[dict[str, str]]:
    """The last HISTORY messages as the model needs them: starting with the person's message
    and alternating (two in a row from one side, e.g. after a lost reply, are joined)."""
    recent = messages[-HISTORY:]
    while recent and recent[0].role != "user":
        recent = recent[1:]
    out: list[dict[str, str]] = []
    for message in recent:
        if out and out[-1]["role"] == message.role:
            joined = f"{out[-1]['content']}\n\n{message.content}"
            out[-1]["content"] = joined[:MAX_CONTENT]
        else:
            out.append({"role": message.role, "content": message.content[:MAX_CONTENT]})
    return out


def week_context(
    view: subscriptions_api.WeekView | None, handles: dict[str, subscriptions_api.WeekLineView]
) -> dict[str, Any] | None:
    """The trimmed week the ML service sees (M13 §3): what the tools need, nothing personal."""
    if view is None:
        return None
    return {
        "delivery_date": view.delivery_date.isoformat(),
        "status": view.status,
        "cutoff_at": view.cutoff_at.isoformat(),
        "can_edit": view.can_edit,
        "total_paise": view.total_paise,
        "lines": [
            {
                "handle": handle,
                "slug": line.slug,
                "name": line.name,
                "qty": line.qty,
                "unit": line.unit,
                "unit_price_paise": line.unit_price_paise,
                "line_total_paise": line.line_total_paise,
                "reason_code": line.reason_code,
            }
            for handle, line in handles.items()
        ],
        "meal_plan": [
            {"date": day, "slot": slot, "dishes": list(dishes)} for day, slot, dishes in view.meals
        ],
    }


@dataclass
class Prepared:
    """A turn between its two transactions: what was stored, what goes out, and what the
    intents are resolved against (the per-turn line handles never leave the API's memory)."""

    chat: Session
    day: date
    lang: Language
    hub_id: uuid.UUID | None
    terms: identity_api.PrivateTerms
    view: subscriptions_api.WeekView | None
    handles: dict[str, subscriptions_api.WeekLineView]
    body: dict[str, Any]


async def prepare_turn(
    session: AsyncSession,
    settings: Settings,
    chat: Session,
    *,
    text: str,
    hub_id: uuid.UUID | None,
    lang: Language,
) -> Prepared:
    """Reserve the turn under the cap, store the person's message scrubbed, and build what the
    ML service gets. The caller commits before asking the model."""
    day = today_ist()
    await reserve_turn(session, chat.user_id, day, settings.assistant_daily_tokens)
    terms = await identity_api.private_terms(session, chat.household_id)
    session.add(
        Message(session_id=chat.id, role="user", content=scrub(text, terms.names, terms.places))
    )
    chat.last_message_at = now()
    await session.flush()
    history = window(await messages_of(session, chat.id))
    cycle = await subscriptions_api.current_cycle_of(session, chat.household_id)
    view = (
        None if cycle is None else await subscriptions_api.week_view(session, cycle, hub_id, lang)
    )
    handles = {f"L{i}": line for i, line in enumerate(view.lines if view else (), start=1)}
    body = {"locale": chat.locale, "messages": history, "week": week_context(view, handles)}
    return Prepared(chat, day, lang, hub_id, terms, view, handles, body)


async def ask(
    http: httpx2.AsyncClient, settings: Settings, body: dict[str, Any]
) -> dict[str, Any] | None:
    """The ML service's answer, or None (the fixed fallback reply) when it is unset, down,
    slow or answers something malformed."""
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
        answer = response.json()
    except (httpx2.HTTPError, ValueError) as exc:
        log.warning("assistant_fallback", reason=type(exc).__name__)
        return None
    if not isinstance(answer, dict) or not isinstance(answer.get("reply"), str):
        log.warning("assistant_fallback", reason="malformed")
        return None
    return answer


async def finish_turn(
    session: AsyncSession, prepared: Prepared, answer: dict[str, Any] | None
) -> tuple[Message, list[Proposal], str, int]:
    """Store the reply and the proposals resolved from the intents; add the tokens. Returns
    (reply, proposals, source, tokens today)."""
    chat = prepared.chat
    source = "ml" if answer is not None else "fallback"
    if answer is None:
        answer = {
            "reply": FALLBACK_REPLY.get(chat.locale[:2], FALLBACK_REPLY["en"]),
            "intents": [],
            "tool_calls": [],
            "usage": {},
            "model": None,
        }
    proposals: list[Proposal] = []
    notes: list[str] = []
    for intent in list(answer.get("intents") or [])[:MAX_INTENTS]:
        if not isinstance(intent, dict):
            continue
        outcome = await resolve(session, prepared, intent)
        if isinstance(outcome, Proposal):
            proposals.append(outcome)
        elif outcome and outcome not in notes:
            notes.append(outcome)
    content = "\n\n".join([str(answer["reply"]).strip() or "…", *notes])
    usage = answer.get("usage") or {}
    tokens_in = int(usage.get("input_tokens", 0) or 0)  # uncached input, as before
    tokens_out = int(usage.get("output_tokens", 0) or 0)
    reply = Message(
        session_id=chat.id,
        role="assistant",
        content=scrub(content, prepared.terms.names, prepared.terms.places),
        tool_calls=[str(t) for t in answer.get("tool_calls", [])],
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    session.add(reply)
    session.add_all(proposals)
    if answer.get("model"):
        chat.model = str(answer["model"])[:40]
    chat.last_message_at = now()
    tokens_today = await record_tokens(session, chat.user_id, prepared.day, tokens_in, tokens_out)
    await session.flush()
    for row in (reply, *proposals):
        await session.refresh(row)
    return reply, proposals, source, tokens_today


# --- intents into proposals ---------------------------------------------------------------------


def _say(lang: Language, key: str, **values: Any) -> str:
    return CLARIFY.get(lang, CLARIFY["en"])[key].format(**values)


def _summary(lang: Language, key: str, on: date, **values: Any) -> str:
    text = SUMMARY.get(lang, SUMMARY["en"])[key].format(date=f"{on.day} {on:%b}", **values)
    return text[:200]


def _steps(value: Any) -> int | None:
    """A whole number of steps within M3's bounds, or None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= subscriptions_api.MAX_QTY else None


def _choose(
    matches: list[catalog_api.ProductMatch],
) -> tuple[catalog_api.ProductMatch | None, list[catalog_api.ProductMatch]]:
    """The one product meant, or the candidates to ask about: an exact spelling wins; else a
    clear best match (well ahead of the next)."""
    exact = [m for m in matches if m.exact]
    if len(exact) == 1:
        return exact[0], []
    if matches and matches[0].score >= 0.8:
        runner_up = matches[1].score if len(matches) > 1 else 0.0
        if runner_up <= matches[0].score - 0.2:
            return matches[0], []
    return None, matches[:3]


def _proposal(prepared: Prepared, action: str, payload: dict[str, Any], summary: str) -> Proposal:
    chat = prepared.chat
    return Proposal(
        session_id=chat.id,
        user_id=chat.user_id,
        household_id=chat.household_id,
        action=action,
        payload=payload,
        summary=summary,
        expires_at=now() + PROPOSAL_TTL,
    )


async def resolve(
    session: AsyncSession, prepared: Prepared, intent: dict[str, Any]
) -> Proposal | str | None:
    """One intent from the model as a proposal card, or what to ask the person instead."""
    lang, view = prepared.lang, prepared.view
    if intent.get("action") == "skip_week":
        return await _resolve_skip(session, prepared, intent)
    if intent.get("action") != "edit_box_line":
        return None
    if view is None or not view.can_edit:
        return _say(lang, "not_editable")
    week = {
        "cycle_id": str(view.cycle_id),
        "version": view.version,
        "delivery_date": view.delivery_date.isoformat(),
    }
    op = intent.get("op")
    if op in ("remove", "qty"):
        slug = str(intent.get("product") or "").strip().lower()
        line = prepared.handles.get(str(intent.get("line") or ""))
        if line is None and slug:
            line = next((ln for ln in view.lines if ln.slug == slug), None)
        if line is None:
            return _say(lang, "not_in_box")
        if op == "remove":
            summary = _summary(lang, "remove", view.delivery_date, name=line.name)
            return _proposal(
                prepared,
                "edit_box_line",
                {
                    "op": "remove",
                    "product": line.slug,
                    "product_id": str(line.product_id),
                    "qty": None,
                    "price_change_paise": -line.line_total_paise,
                    **week,
                },
                summary,
            )
        qty = _steps(intent.get("qty"))
        if qty is None:
            return _say(lang, "how_many", max=subscriptions_api.MAX_QTY)
        return _proposal(
            prepared,
            "edit_box_line",
            {
                "op": "qty",
                "product": line.slug,
                "product_id": str(line.product_id),
                "qty": qty,
                "price_change_paise": (qty - line.qty) * line.unit_price_paise,
                **week,
            },
            _summary(lang, "qty", view.delivery_date, name=line.name, qty=qty, unit=line.unit),
        )
    if op != "add":
        return None
    words = str(intent.get("product") or "").strip()
    if not words:
        return _say(lang, "add_what")
    given = intent.get("qty")
    qty = 1 if given is None else _steps(given)
    if qty is None:
        return _say(lang, "how_many", max=subscriptions_api.MAX_QTY)
    product, candidates = _choose(await catalog_api.find_products(session, words, prepared.hub_id))
    if product is None:
        if not candidates:
            return _say(lang, "unknown", product=words[:60])
        options = ", ".join(localised(c.names, lang) for c in candidates)
        return _say(lang, "which", options=options)
    name = localised(product.names, lang)
    unit = catalog_api.step_label(product.step_qty, product.step_unit, lang)
    existing = next((ln for ln in view.lines if ln.product_id == product.id), None)
    if existing is not None:  # already in the box: add on top of what is there
        total = min(subscriptions_api.MAX_QTY, existing.qty + qty)
        return _proposal(
            prepared,
            "edit_box_line",
            {
                "op": "qty",
                "product": product.slug,
                "product_id": str(product.id),
                "qty": total,
                "price_change_paise": (total - existing.qty) * existing.unit_price_paise,
                **week,
            },
            _summary(lang, "qty", view.delivery_date, name=name, qty=total, unit=existing.unit),
        )
    return _proposal(
        prepared,
        "edit_box_line",
        {
            "op": "add",
            "product": product.slug,
            "product_id": str(product.id),
            "qty": qty,
            "price_change_paise": qty * product.price_paise,
            **week,
        },
        _summary(lang, "add", view.delivery_date, name=name, qty=qty, unit=unit),
    )


async def _resolve_skip(
    session: AsyncSession, prepared: Prepared, intent: dict[str, Any]
) -> Proposal | str:
    """ "this" is the coming delivery the person sees; "next" the household's week after it."""
    lang, view = prepared.lang, prepared.view
    which = "next" if intent.get("week") == "next" else "this"
    if view is None:
        return _say(lang, "no_week")
    if which == "this":
        if not view.can_edit:
            return _say(lang, "not_editable")
        cycle_id, version, on = view.cycle_id, view.version, view.delivery_date
    else:
        later = await subscriptions_api.cycle_after(
            session, prepared.chat.household_id, view.delivery_date
        )
        if later is None:
            return _say(lang, "no_week")
        cycle_id, version, on = later.id, later.version, later.delivery_date
    return _proposal(
        prepared,
        "skip_week",
        {
            "week": which,
            "cycle_id": str(cycle_id),
            "version": version,
            "delivery_date": on.isoformat(),
        },
        _summary(lang, "skip", on),
    )


# --- confirm, reject, expire --------------------------------------------------------------------


async def get_proposal(
    session: AsyncSession, user_id: uuid.UUID, proposal_id: uuid.UUID, *, lock: bool = False
) -> Proposal:
    """The user's own proposal; `lock` holds its row (FOR UPDATE) so two confirms of one card
    cannot both apply: the second waits, then finds it decided."""
    query = select(Proposal).where(Proposal.id == proposal_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    found = (await session.execute(query)).scalar_one_or_none()
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
    """Execute the proposed change through M3's facade against the week and version the card
    was made from (M3's own 412 `subscriptions.version_conflict` if the week changed since)."""
    _decided(proposal)
    payload = proposal.payload
    if not payload.get("cycle_id"):
        raise ProblemError(409, "assistant.no_cycle", "There is no week to change right now.")
    cycle_id = uuid.UUID(str(payload["cycle_id"]))
    version = int(payload["version"])
    if proposal.action == "skip_week":
        result = await subscriptions_api.skip_cycle(
            session, proposal.household_id, cycle_id, expected_version=version
        )
    else:
        edit = subscriptions_api.LineEdit(
            str(payload["op"]),
            uuid.UUID(str(payload["product_id"])),
            qty=int(payload["qty"]) if payload.get("qty") is not None else None,
        )
        result = await subscriptions_api.edit_cycle_lines(
            session, proposal.household_id, cycle_id, [edit], expected_version=version
        )
    proposal.status = "confirmed"
    proposal.decided_at = now()
    proposal.result = result
    await _carry_version(session, proposal, version, int(result["version"]))
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


async def _carry_version(session: AsyncSession, confirmed: Proposal, old: int, new: int) -> None:
    """The other pending cards of this conversation made from the same week and version were
    shown next to the one just confirmed: the person saw that change, so they move to the new
    version. Cards being confirmed this moment (locked) are skipped and get M3's 412."""
    siblings = (
        await session.execute(
            select(Proposal)
            .where(
                Proposal.session_id == confirmed.session_id,
                Proposal.status == "pending",
                Proposal.id != confirmed.id,
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars()
    for sibling in siblings:
        same_week = sibling.payload.get("cycle_id") == confirmed.payload.get("cycle_id")
        if same_week and sibling.payload.get("version") == old:
            sibling.payload = {**sibling.payload, "version": new}


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
