"""HTTP endpoints of M13 Assistant (Ghar Saathi, spec §3): a message in, the reply with proposal
cards; the session's history; confirm or reject a proposal."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status

from gharbasket.modules.assistant import jobs as _jobs  # noqa: F401 (registers the job)
from gharbasket.modules.assistant import permissions as _permissions  # noqa: F401
from gharbasket.modules.assistant import service
from gharbasket.modules.assistant.models import Message, Proposal
from gharbasket.modules.assistant.schemas import (
    ConfirmOut,
    MessageIn,
    MessageOut,
    ProposalOut,
    SessionOut,
    TurnOut,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.web_session import ACCESS_COOKIE

router = APIRouter(tags=["assistant"])

CanUse = Annotated[Permit, require("assistant:use")]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _message_out(message: Message) -> MessageOut:
    return MessageOut(
        id=message.id,
        role=message.role,  # pyright: ignore[reportArgumentType]
        content=message.content,
        tool_calls=list(message.tool_calls or []),
        created_at=message.created_at,
    )


def _proposal_out(proposal: Proposal) -> ProposalOut:
    return ProposalOut(
        id=proposal.id,
        action=proposal.action,  # pyright: ignore[reportArgumentType]
        payload=proposal.payload,
        summary=proposal.summary,
        status=proposal.status,  # pyright: ignore[reportArgumentType]
        expires_at=proposal.expires_at,
        result=proposal.result,
    )


def _user_token(request: Request) -> str:
    """The caller's own access token, passed to the ML service so the tools act as them."""
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token:
        return token
    return request.cookies.get(ACCESS_COOKIE, "")


async def _household(session: DbSession, permit: Permit) -> identity_api.HouseholdRef:
    household = await identity_api.household_for_user(session, permit.principal.user_id)
    if household is None:
        raise service.not_found("assistant.not_found", "Onboard a household first.")
    return household


@router.post(
    "/assistant/messages",
    summary="Say something to Ghar Saathi; the reply may carry proposal cards to confirm",
    responses={
        404: _problem("assistant.not_found"),
        429: _problem("assistant.daily_cap"),
    },
)
async def post_message(
    body: MessageIn,
    permit: CanUse,
    session: DbSession,
    settings: AppSettings,
    lang: Lang,
    request: Request,
) -> TurnOut:
    household = await _household(session, permit)
    user_id = permit.principal.user_id
    if body.session_id is None:
        chat = await service.start_session(session, user_id, household.id, str(lang))
    else:
        chat = await service.get_session(session, user_id, body.session_id)
    base_url = settings.api_public_url or str(request.base_url).rstrip("/")
    async with service.http_client(getattr(request.app.state, "http", None)) as http:
        reply, proposals, source, usage = await service.turn(
            session,
            http,
            settings,
            chat,
            text=body.text,
            user_token=_user_token(request),
            api_base_url=base_url,
        )
    out = TurnOut(
        session_id=chat.id,
        reply=_message_out(reply),
        proposals=[_proposal_out(p) for p in proposals],
        source=source,  # pyright: ignore[reportArgumentType]
        tokens_today=usage.tokens_in + usage.tokens_out,
        tokens_cap=settings.assistant_daily_tokens,
    )
    await session.commit()
    return out


@router.get(
    "/assistant/sessions/{session_id}",
    summary="A conversation's history and its proposal cards",
    responses={404: _problem("assistant.not_found")},
)
async def get_session(session_id: uuid.UUID, permit: CanUse, session: DbSession) -> SessionOut:
    chat = await service.get_session(session, permit.principal.user_id, session_id)
    return SessionOut(
        id=chat.id,
        locale=chat.locale,
        model=chat.model,
        created_at=chat.created_at,
        messages=[_message_out(m) for m in await service.messages_of(session, chat.id)],
        proposals=[_proposal_out(p) for p in await service.proposals_of(session, chat.id)],
    )


@router.post(
    "/assistant/confirm/{proposal_id}",
    summary="Confirm a proposal: the change is made through the owning module (Idempotency-Key)",
    responses={
        404: _problem("assistant.proposal_not_found"),
        409: _problem(
            "assistant.proposal_decided, assistant.proposal_expired, assistant.no_cycle, "
            "subscriptions.cutoff_passed, subscriptions.not_editable"
        ),
        422: _problem("assistant.product_unknown, subscriptions.qty_invalid"),
    },
)
async def confirm_proposal(
    proposal_id: uuid.UUID, permit: CanUse, session: DbSession, idem: Idempotent
) -> ConfirmOut:
    proposal = await service.get_proposal(session, permit.principal.user_id, proposal_id)
    await service.confirm(session, proposal, actor_user_id=permit.principal.user_id)
    out = ConfirmOut(proposal=_proposal_out(proposal))
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/assistant/proposals/{proposal_id}/reject",
    status_code=status.HTTP_200_OK,
    summary="Reject a proposal; nothing changes",
    responses={
        404: _problem("assistant.proposal_not_found"),
        409: _problem("assistant.proposal_decided, assistant.proposal_expired"),
    },
)
async def reject_proposal(
    proposal_id: uuid.UUID, permit: CanUse, session: DbSession
) -> ProposalOut:
    proposal = await service.get_proposal(session, permit.principal.user_id, proposal_id)
    await service.reject(session, proposal)
    out = _proposal_out(proposal)
    await session.commit()
    return out
