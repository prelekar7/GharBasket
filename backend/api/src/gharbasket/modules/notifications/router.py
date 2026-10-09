"""HTTP endpoints of M11 Notifications: the inbox and preferences (spec §3.1). Any signed-in
person has an inbox, whatever their role, so these need a token but no permission."""

import uuid
from typing import Any

from fastapi import APIRouter, Response, status

from gharbasket.modules.notifications import jobs as _jobs  # noqa: F401 (registers jobs, consumers)
from gharbasket.modules.notifications import service
from gharbasket.modules.notifications.schemas import InboxOut, PreferencesIn, PreferencesOut
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.locale import Lang
from gharbasket.platform.pagination import Cursor, Limit

router = APIRouter(tags=["notifications"])


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
    return await service.save_preferences(session, principal.user_id, body)
