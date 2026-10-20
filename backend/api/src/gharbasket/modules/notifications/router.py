"""HTTP endpoints of M11 Notifications: the inbox and preferences (spec §3.1), which any signed-in
person has whatever their role; the console's templates and delivery log, and WhatsApp's webhook
(§3.2)."""

import hmac
import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

from gharbasket.modules.notifications import admin, receipts, service
from gharbasket.modules.notifications import jobs as _jobs  # noqa: F401 (registers jobs, consumers)
from gharbasket.modules.notifications import permissions as _permissions  # noqa: F401 (rules)
from gharbasket.modules.notifications.schemas import (
    DeliveryLogOut,
    InboxOut,
    PreferencesIn,
    PreferencesOut,
    PreviewOut,
    SavedTemplatesOut,
    TemplateIn,
    TemplatesOut,
    WhatsAppAck,
)
from gharbasket.modules.notifications.whatsapp import valid_signature
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Lang
from gharbasket.platform.pagination import Cursor, Limit
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["notifications"])

CanWriteTemplates = Annotated[Permit, require("templates:write")]
CanSupport = Annotated[Permit, require("notifications:support")]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


@router.get(
    "/notifications",
    summary="The caller's inbox, newest first, in the request language",
    responses={401: _problem("auth.invalid_token"), 422: _problem("request.invalid_cursor")},
)
async def list_notifications(
    principal: CurrentPrincipal,
    session: DbSession,
    response: Response,
    lang: Lang,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> InboxOut:
    items, unread, next_cursor = await service.inbox(
        session, principal.user_id, lang, cursor, limit
    )
    response.headers["Cache-Control"] = "no-store"
    return InboxOut(items=items, unread=unread, next_cursor=next_cursor)


@router.post(
    "/notifications/read-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark the whole inbox read",
    responses={401: _problem("auth.invalid_token")},
)
async def read_all(principal: CurrentPrincipal, session: DbSession) -> None:
    await service.mark_all_read(session, principal.user_id)
    await session.commit()


@router.post(
    "/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark one message read (the first read time is kept)",
    responses={401: _problem("auth.invalid_token"), 404: _problem("notifications.not_found")},
)
async def read_one(
    notification_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession
) -> None:
    await service.mark_read(session, principal.user_id, notification_id)
    await session.commit()


@router.get(
    "/notifications/preferences",
    summary="Push and WhatsApp per category, and quiet hours",
    responses={401: _problem("auth.invalid_token")},
)
async def get_preferences(principal: CurrentPrincipal, session: DbSession) -> PreferencesOut:
    return await service.preferences(session, principal.user_id)


@router.put(
    "/notifications/preferences",
    summary="Save push and WhatsApp per category, and optionally quiet hours",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("notifications.whatsapp_consent_required"),
        422: _problem("notifications.category_unknown, notifications.quiet_hours_invalid"),
    },
)
async def put_preferences(
    body: PreferencesIn, principal: CurrentPrincipal, session: DbSession
) -> PreferencesOut:
    saved = await service.save_preferences(session, principal.user_id, body)
    await session.commit()
    return saved


# --- the console (§3.2) ------------------------------------------------------------------------

TEMPLATES = "/admin/notification-templates"


@router.get(TEMPLATES, summary="Notification templates: the active versions, or every version")
async def list_templates(
    permit: CanWriteTemplates,
    session: DbSession,
    kind: Annotated[str | None, Query(max_length=64)] = None,
    channel: Annotated[str | None, Query(max_length=8)] = None,
    locale: Annotated[str | None, Query(max_length=5)] = None,
    history: bool = False,
) -> TemplatesOut:
    return await admin.templates(session, kind, channel, locale, history)


@router.put(
    TEMPLATES,
    summary="Save a template in all three locales as a new active version",
    responses={422: _problem("notifications.template_invalid")},
)
async def put_templates(
    body: TemplateIn, permit: CanWriteTemplates, session: DbSession
) -> SavedTemplatesOut:
    return SavedTemplatesOut(items=await admin.save(session, body, permit.principal.user_id))


@router.post(
    f"{TEMPLATES}/preview",
    summary="Render a template in all three locales with sample values (nothing is saved)",
    responses={422: _problem("notifications.template_invalid")},
)
async def preview_templates(body: TemplateIn, permit: CanWriteTemplates) -> PreviewOut:
    return PreviewOut(items=admin.preview(body))


@router.get(
    "/admin/notifications",
    summary="Delivery log: a person's messages with their delivery attempts",
    responses={422: _problem("request.invalid_cursor")},
)
async def delivery_log(
    user_id: uuid.UUID,
    permit: CanSupport,
    session: DbSession,
    response: Response,
    lang: Lang,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> DeliveryLogOut:
    items, next_cursor = await admin.delivery_log(session, permit, user_id, lang, cursor, limit)
    response.headers["Cache-Control"] = "no-store"
    return DeliveryLogOut(items=items, next_cursor=next_cursor)


# --- WhatsApp's webhook (§3.2) -----------------------------------------------------------------


def _whatsapp_unavailable() -> ProblemError:
    return ProblemError(
        503, "notifications.whatsapp_unavailable", "WhatsApp is not set up on this server."
    )


@router.get(
    "/webhooks/whatsapp",
    response_class=PlainTextResponse,
    summary="Meta's webhook handshake (verify token); not for the apps",
    responses={
        403: _problem("notifications.bad_verify_token"),
        503: _problem("notifications.whatsapp_unavailable: no verify token configured"),
    },
)
async def whatsapp_handshake(
    settings: AppSettings,
    mode: Annotated[str, Query(alias="hub.mode", max_length=20)],
    verify_token: Annotated[str, Query(alias="hub.verify_token", max_length=200)],
    challenge: Annotated[str, Query(alias="hub.challenge", max_length=200)],
) -> PlainTextResponse:
    expected = settings.whatsapp_verify_token
    if expected is None:
        raise _whatsapp_unavailable()
    same = hmac.compare_digest(verify_token.encode(), expected.get_secret_value().encode())
    if mode != "subscribe" or not same:
        raise ProblemError(403, "notifications.bad_verify_token", "The verify token is wrong.")
    return PlainTextResponse(challenge)


@router.post(
    "/webhooks/whatsapp",
    summary="WhatsApp's webhook (signed by Meta): delivery receipts; not for the apps",
    responses={
        400: _problem("request.invalid: not a JSON object"),
        401: _problem("notifications.bad_signature"),
        503: _problem("notifications.whatsapp_unavailable: no app secret configured"),
    },
)
async def whatsapp_webhook(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    x_hub_signature_256: Annotated[str, Header(max_length=100)],
) -> WhatsAppAck:
    secret = settings.whatsapp_app_secret
    if secret is None:
        raise _whatsapp_unavailable()
    body = await request.body()
    if not valid_signature(body, x_hub_signature_256, secret.get_secret_value()):
        raise ProblemError(
            401, "notifications.bad_signature", "The webhook signature is not valid."
        )
    try:
        event = json.loads(body)
    except ValueError as exc:
        raise ProblemError(400, "request.invalid", "The webhook body is not JSON.") from exc
    if not isinstance(event, dict):
        raise ProblemError(400, "request.invalid", "The webhook body is not a JSON object.")
    return WhatsAppAck(outcomes=await receipts.receive(session, event))
