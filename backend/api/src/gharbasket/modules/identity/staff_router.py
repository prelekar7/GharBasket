"""Staff lookup, revoke and the hub's staff (spec §3.6): the console's Users page, Delivery log
and the franchise Staff page. These show personal data to staff, so each is limited to what the
role needs: the admin lookup is audit-logged and the hub list masks phones."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from gharbasket.modules.identity import staff_admin
from gharbasket.modules.identity.schemas import (
    AdminUser,
    AnyRole,
    HubStaff,
    PendingGrant,
    normalise_phone,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["identity"])

AdminLimit = Annotated[int, Query(ge=1, le=50, description="Items per page (default 20)")]
StaffLimit = Annotated[int, Query(ge=1, le=200, description="Items per page (default 100)")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _invalid(detail: str) -> ProblemError:
    return ProblemError(422, "request.invalid", detail)


@router.get(
    "/admin/users",
    summary="Admin: find a user by phone or name, list the staff or the pending grants "
    "(audit-logged)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem(
            "request.invalid (not exactly one filter, a bad number), request.invalid_cursor"
        ),
    },
    response_model=Page[AdminUser] | Page[PendingGrant],
)
async def admin_users(
    permit: Annotated[Permit, require("users:read")],
    session: DbSession,
    response: Response,
    phone: Annotated[
        str | None, Query(max_length=20, description="An Indian mobile, any usual way of typing")
    ] = None,
    q: Annotated[str | None, Query(description="2 to 60 characters of a name")] = None,
    staff: Annotated[bool | None, Query(description="true: everyone holding a staff role")] = None,
    pending: Annotated[
        bool | None, Query(description="true: staff roles waiting for a sign-in")
    ] = None,
    cursor: Cursor = None,
    limit: AdminLimit = 20,
) -> Page[AdminUser] | Page[PendingGrant]:
    given = [
        name
        for name, value in (("phone", phone), ("q", q), ("staff", staff), ("pending", pending))
        if value is not None
    ]
    if len(given) != 1:
        raise _invalid("Send exactly one of phone, q, staff or pending.")
    if staff is False or pending is False:
        raise _invalid("staff and pending take only true.")
    _no_store(response)
    admin_id = permit.principal.user_id

    result: Page[AdminUser] | Page[PendingGrant]
    if phone is not None:
        try:
            number = normalise_phone(phone)
        except ValueError as exc:
            raise _invalid("That is not a valid phone number.") from exc
        result = Page(
            items=await staff_admin.users_by_phone(session, admin_id, number), next_cursor=None
        )
    elif q is not None:
        typed = " ".join(q.split())
        if not 2 <= len(typed) <= 60:
            raise _invalid("The name to look for must be 2 to 60 characters.")
        users, next_cursor = await staff_admin.users_by_name(
            session, admin_id, typed, cursor=cursor, limit=limit
        )
        result = Page(items=users, next_cursor=next_cursor)
    elif staff:
        users, next_cursor = await staff_admin.staff_users(
            session, admin_id, cursor=cursor, limit=limit
        )
        result = Page(items=users, next_cursor=next_cursor)
    else:
        grants, next_cursor = await staff_admin.pending_grants(
            session, admin_id, cursor=cursor, limit=limit
        )
        result = Page(items=grants, next_cursor=next_cursor)
    await session.commit()  # the audit entry
    return result


@router.delete(
    "/admin/user-roles/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Admin: revoke a staff grant or cancel a pending one (audit-logged; reaches the "
    "person's token within 15 minutes)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem(
            "auth.forbidden, auth.role_not_granted, identity.not_admin (the admin role was removed)"
        ),
        404: _problem("identity.role_not_found"),
        409: _problem(
            "identity.role_not_removable (customer or partner role), "
            "identity.cannot_remove_own_admin, identity.last_admin"
        ),
    },
)
async def revoke_role(
    grant_id: uuid.UUID, permit: Annotated[Permit, require("roles:revoke")], session: DbSession
) -> None:
    await staff_admin.revoke_grant(session, grant_id, permit.principal.user_id)
    await session.commit()


@router.get(
    "/hubs/{hub_id}/staff",
    summary="The hub's role grants: names and masked phones (hub staff of the hub, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("request.invalid_cursor, request.invalid"),
    },
)
async def hub_staff(
    hub_id: uuid.UUID,
    permit: Annotated[Permit, require("staff:read")],
    session: DbSession,
    response: Response,
    role: Annotated[AnyRole | None, Query()] = None,
    cursor: Cursor = None,
    limit: StaffLimit = 100,
) -> Page[HubStaff]:
    permit.ensure(hub_id)
    _no_store(response)
    items, next_cursor = await staff_admin.hub_staff(
        session, hub_id, role=role, cursor=cursor, limit=limit
    )
    return Page(items=items, next_cursor=next_cursor)
