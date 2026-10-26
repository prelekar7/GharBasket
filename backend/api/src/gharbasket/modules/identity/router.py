"""HTTP endpoints of M1 Identity & Household: sign-in, tokens and the signed-in user.

Sign-in is phone OTP: the app verifies the OTP with Firebase and sends the resulting ID token to
`POST /v1/auth/exchange`. The first exchange for a phone number creates the account.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select

from gharbasket.modules.identity import deletion, service, staff_admin
from gharbasket.modules.identity import jobs as _jobs  # noqa: F401 (registers the jobs)
from gharbasket.modules.identity import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.identity.exports import router as exports_router
from gharbasket.modules.identity.firebase import FirebaseTokenVerifier
from gharbasket.modules.identity.household_router import router as household_router
from gharbasket.modules.identity.models import (
    Deletion,
    LoginSession,
    PendingRoleGrant,
    User,
    UserRole,
)
from gharbasket.modules.identity.schemas import (
    DeletionOut,
    DeviceOut,
    ExchangeIn,
    ExchangeOut,
    MeOut,
    MeUpdate,
    PendingGrant,
    PushTokenIn,
    RefreshIn,
    RoleGrantIn,
    RoleGrantOut,
    RoleOut,
    TokensOut,
    UserOut,
)
from gharbasket.modules.identity.staff_router import router as staff_router
from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.revocations import mark_revoked
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.tokens import invalid_token
from gharbasket.platform.valkey import Valkey
from gharbasket.platform.web_session import (
    REFRESH_COOKIE,
    check_web_request,
    clear_session_cookies,
    set_session_cookies,
    wants_cookies,
)

router = APIRouter(tags=["identity"])
router.include_router(household_router)
router.include_router(staff_router)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def get_firebase_verifier(request: Request) -> FirebaseTokenVerifier | None:
    return getattr(request.app.state, "firebase_verifier", None)


FirebaseVerifier = Annotated[FirebaseTokenVerifier | None, Depends(get_firebase_verifier)]


# Sign-in attempts per client address and per phone number (keys never hold the number itself).
EXCHANGE_PER_IP = (30, 600)
EXCHANGE_PER_PHONE = (10, 600)
REFRESH_PER_IP = (300, 60)


def _client_ip(request: Request) -> str:
    # On Render, uvicorn runs with --proxy-headers, so this is the real caller's address.
    return request.client.host if request.client else "unknown"


def _deliver(
    request: Request, response: Response, issued: service.IssuedTokens
) -> tuple[str | None, str | None]:
    """Browsers get httpOnly cookies and no tokens in the body; apps get the tokens."""
    if not wants_cookies(request):
        return issued.access_token, issued.refresh_token
    refresh_max_age = int((issued.session_expires_at - datetime.now(UTC)).total_seconds())
    set_session_cookies(
        response,
        access_token=issued.access_token,
        access_max_age=issued.access_expires_in,
        refresh_token=issued.refresh_token,
        refresh_max_age=max(refresh_max_age, 0),
    )
    return None, None


@router.post(
    "/auth/exchange",
    summary="Sign in (or sign up) with a verified phone number",
    responses={
        401: _problem("auth.invalid_token: the Firebase ID token is not valid"),
        403: _problem("auth.dev_login_disabled, auth.account_disabled or auth.csrf_rejected"),
        409: _problem(
            "identity.deletion_pending (with purge_on): send keep_account to keep it; "
            "identity.account_deleted: the account was purged meanwhile"
        ),
        429: _problem("auth.rate_limited (see Retry-After)"),
        503: _problem("auth.provider_unavailable: Firebase keys could not be fetched"),
    },
)
async def exchange(
    body: ExchangeIn,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    verifier: FirebaseVerifier,
    valkey: Valkey,
) -> ExchangeOut:
    if wants_cookies(request):
        check_web_request(request, settings)
    await rate_limit.enforce(
        valkey,
        f"exchange:ip:{_client_ip(request)}",
        limit=EXCHANGE_PER_IP[0],
        window_s=EXCHANGE_PER_IP[1],
    )
    if body.dev_phone is not None:
        if not settings.dev_login_enabled:
            raise ProblemError(
                403, "auth.dev_login_disabled", "Dev login works only on a local backend."
            )
        phone, auth_time = body.dev_phone, None
    else:
        if verifier is None:
            raise ProblemError(
                503, "auth.provider_unavailable", "Phone sign-in is not configured here."
            )
        assert body.firebase_id_token is not None  # guaranteed by ExchangeIn
        verified = await verifier.verify(body.firebase_id_token)
        phone, auth_time = verified.phone_e164, verified.auth_time
    await rate_limit.enforce(
        valkey,
        f"exchange:phone:{rate_limit.private_key(phone)}",
        limit=EXCHANGE_PER_PHONE[0],
        window_s=EXCHANGE_PER_PHONE[1],
    )

    user, created = await service.find_or_create_user(session, phone)
    cancelled = user.status == "deleting"
    if cancelled:
        scheduled = await deletion.for_sign_in(session, user.id)
        if not body.keep_account:
            raise ProblemError(
                409,
                "identity.deletion_pending",
                "This account is being deleted. Do you want to keep it?",
                extra={"purge_on": scheduled.purge_on.isoformat()},
            )
        await deletion.cancel(session, user, scheduled)
    if user.status != "active":
        raise ProblemError(403, "auth.account_disabled", "This account is not active.")
    if created and body.dev_name:
        user.name = body.dev_name
    issued = await service.start_session(
        session,
        settings,
        user,
        platform=body.device.platform,
        push_token=body.device.push_token,
        auth_time=auth_time,
    )
    await session.commit()
    access_token, refresh_token = _deliver(request, response, issued)
    return ExchangeOut(
        deletion_cancelled=cancelled,
        access_token=access_token,
        access_expires_in=issued.access_expires_in,
        refresh_token=refresh_token,
        is_new_user=created,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/auth/refresh",
    summary="Exchange a refresh token for new tokens (the old refresh token stops working)",
    responses={
        401: _problem("auth.invalid_token, or auth.refresh_reused (session ended)"),
        403: _problem("auth.csrf_rejected"),
        429: _problem("auth.rate_limited (see Retry-After)"),
    },
)
async def refresh(
    body: RefreshIn,
    request: Request,
    response: Response,
    session: DbSession,
    settings: SettingsDep,
    valkey: Valkey,
) -> TokensOut:
    presented = body.refresh_token
    if presented is None:
        # Browsers: the refresh token is the httpOnly cookie, sent only to /v1/auth/*.
        check_web_request(request, settings)
        presented = request.cookies.get(REFRESH_COOKIE)
        if presented is None:
            raise invalid_token("Sign in first.")
    await rate_limit.enforce(
        valkey,
        f"refresh:ip:{_client_ip(request)}",
        limit=REFRESH_PER_IP[0],
        window_s=REFRESH_PER_IP[1],
    )
    try:
        issued = await service.rotate(session, settings, presented)
    except service.RefreshReused as reused:
        await mark_revoked(valkey, [reused.session_id])
        raise
    await session.commit()
    access_token, refresh_token = _deliver(request, response, issued)
    return TokensOut(
        access_token=access_token,
        access_expires_in=issued.access_expires_in,
        refresh_token=refresh_token,
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign this device out (takes effect at once)",
    responses={401: _problem("auth.invalid_token")},
)
async def logout(
    principal: CurrentPrincipal, response: Response, session: DbSession, valkey: Valkey
) -> None:
    await service.revoke_session(session, principal.session_id)
    await session.commit()
    await mark_revoked(valkey, [principal.session_id])
    clear_session_cookies(response)


@router.post(
    "/auth/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out on every device (takes effect at once)",
    responses={401: _problem("auth.invalid_token")},
)
async def logout_all(
    principal: CurrentPrincipal, response: Response, session: DbSession, valkey: Valkey
) -> None:
    ended = await service.revoke_all_sessions(session, principal.user_id)
    await session.commit()
    await mark_revoked(valkey, ended)
    clear_session_cookies(response)


def _deletion_out(scheduled: Deletion) -> DeletionOut:
    # wallet_refund comes with M5's close_wallet (M5 §3.3); until then the purge waits for a
    # zero balance instead.
    return DeletionOut(
        status="scheduled",
        requested_at=scheduled.requested_at,
        purge_on=scheduled.purge_on,
        wallet_refund=None,
    )


@router.delete(
    "/me",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Delete my account after a 30-day grace (signs out every device; spec §3.5)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("identity.fresh_otp_required (with max_age_s), or auth.csrf_rejected"),
        409: _problem("identity.staff_account (with roles): an admin removes staff roles first"),
    },
)
async def delete_me(
    principal: CurrentPrincipal, response: Response, session: DbSession, valkey: Valkey
) -> DeletionOut:
    user = await session.get(User, principal.user_id)
    login = await session.get(LoginSession, principal.session_id)
    if user is None or login is None:
        raise invalid_token()
    if user.status == "deleting" and login.revoke_reason == "account_deleted":
        # A retried request whose first try already ended this session: the same answer.
        scheduled = await deletion.pending(session, user.id)
        if scheduled is not None:
            return _deletion_out(scheduled)
    if user.status != "active" or login.revoked_at is not None:
        raise invalid_token()
    scheduled, ended = await deletion.request(session, user, login)
    await session.commit()
    await mark_revoked(valkey, ended)
    clear_session_cookies(response)
    return _deletion_out(scheduled)


async def _signed_in_user(principal: CurrentPrincipal, session: DbSession) -> User:
    """The caller's account, refusing users who are blocked or whose device was signed out."""
    user = await session.get(User, principal.user_id)
    login = await session.get(LoginSession, principal.session_id)
    if user is None or user.status != "active" or login is None or login.revoked_at is not None:
        raise invalid_token()
    return user


SignedInUser = Annotated[User, Depends(_signed_in_user)]
router.include_router(exports_router, dependencies=[Depends(_signed_in_user)])


async def _roles(session: DbSession, user_id: uuid.UUID) -> list[RoleOut]:
    roles = await session.execute(
        select(UserRole).where(UserRole.user_id == user_id).order_by(UserRole.created_at)
    )
    return [RoleOut.model_validate(role) for role in roles.scalars()]


@router.get(
    "/me",
    summary="The signed-in user and all their roles",
    responses={401: _problem("auth.invalid_token")},
)
async def me(user: SignedInUser, session: DbSession) -> MeOut:
    return MeOut(**UserOut.model_validate(user).model_dump(), roles=await _roles(session, user.id))


@router.patch(
    "/me",
    summary="Change name, language or senior mode (only the fields sent)",
    responses={401: _problem("auth.invalid_token")},
)
async def update_me(body: MeUpdate, user: SignedInUser, session: DbSession) -> MeOut:
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.commit()
    await session.refresh(user)
    return MeOut(**UserOut.model_validate(user).model_dump(), roles=await _roles(session, user.id))


@router.get(
    "/me/roles",
    summary="All roles of the signed-in user, with scope and status (for the role switcher)",
    responses={401: _problem("auth.invalid_token")},
)
async def my_roles(user: SignedInUser, session: DbSession) -> list[RoleOut]:
    return await _roles(session, user.id)


@router.get(
    "/me/devices",
    summary="Devices and browsers where this account is signed in",
    responses={401: _problem("auth.invalid_token")},
)
async def my_devices(
    principal: CurrentPrincipal, user: SignedInUser, session: DbSession
) -> list[DeviceOut]:
    return [
        DeviceOut.model_validate(
            {
                "id": device.id,
                "platform": device.platform,
                "signed_in_at": device.created_at,
                "last_seen_at": device.last_seen_at,
                "current": device.id == principal.session_id,
            }
        )
        for device in await service.list_devices(session, user.id)
    ]


@router.delete(
    "/me/devices/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out one device (its refresh token stops working)",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("identity.device_not_found"),
    },
)
async def sign_out_device(
    device_id: uuid.UUID, user: SignedInUser, session: DbSession, valkey: Valkey
) -> None:
    if not await service.sign_out_device(session, user.id, device_id):
        raise ProblemError(404, "identity.device_not_found", "No such signed-in device.")
    await session.commit()
    await mark_revoked(valkey, [device_id])


@router.put(
    "/me/push-token",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Set or clear this device's FCM token for push notifications (M11 §3.1)",
    responses={401: _problem("auth.invalid_token")},
)
async def put_push_token(
    body: PushTokenIn, principal: CurrentPrincipal, user: SignedInUser, session: DbSession
) -> None:
    await service.set_push_token(session, user.id, principal.session_id, body.push_token)
    await session.commit()


@router.post(
    "/admin/user-roles",
    status_code=status.HTTP_201_CREATED,
    summary="Admin: give a user a staff role (Idempotency-Key required; audit-logged)",
    description=(
        "A number with no account and a staff role gets a pending grant (202) that the first "
        "sign-in turns into the role; a partner role still needs the account."
    ),
    responses={
        202: {"model": PendingGrant, "description": "The number has not signed in: waiting"},
        403: _problem("auth.forbidden"),
        404: _problem("identity.user_not_found (a partner role for a number with no account)"),
        409: _problem("identity.role_exists"),
        422: _problem("identity.invalid_scope, request.idempotency_mismatch, request.invalid"),
    },
)
async def grant_role(
    body: RoleGrantIn,
    permit: Annotated[Permit, require("roles:grant")],
    session: DbSession,
    response: Response,
    idem: Idempotent,
) -> RoleGrantOut | PendingGrant:
    granted = await service.grant_role_or_wait(
        session,
        phone_e164=body.phone_e164,
        role=body.role,
        scope_id=body.scope_id,
        granted_by=permit.principal.user_id,
    )
    result: RoleGrantOut | PendingGrant
    if isinstance(granted, PendingRoleGrant):
        result = await staff_admin.pending_view_of(session, granted)
        response.status_code = status.HTTP_202_ACCEPTED
    else:
        result = RoleGrantOut.model_validate(granted)
    await idem.save(response.status_code or status.HTTP_201_CREATED, result)
    await session.commit()
    return result
