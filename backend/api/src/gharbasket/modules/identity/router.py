"""HTTP endpoints of M1 Identity & Household: sign-in, tokens and the signed-in user.

Sign-in is phone OTP: the app verifies the OTP with Firebase and sends the resulting ID token to
`POST /v1/auth/exchange`. The first exchange for a phone number creates the account.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from gharbasket.modules.identity import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.identity import service
from gharbasket.modules.identity.firebase import FirebaseTokenVerifier
from gharbasket.modules.identity.models import LoginSession, User, UserRole
from gharbasket.modules.identity.schemas import (
    DeviceOut,
    ExchangeIn,
    ExchangeOut,
    MeOut,
    MeUpdate,
    RefreshIn,
    RoleGrantIn,
    RoleGrantOut,
    RoleOut,
    TokensOut,
    UserOut,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.tokens import invalid_token

router = APIRouter(tags=["identity"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def get_firebase_verifier(request: Request) -> FirebaseTokenVerifier | None:
    return getattr(request.app.state, "firebase_verifier", None)


FirebaseVerifier = Annotated[FirebaseTokenVerifier | None, Depends(get_firebase_verifier)]


@router.post(
    "/auth/exchange",
    summary="Sign in (or sign up) with a verified phone number",
    responses={
        401: _problem("auth.invalid_token: the Firebase ID token is not valid"),
        403: _problem("auth.dev_login_disabled, or auth.account_disabled"),
        503: _problem("auth.provider_unavailable: Firebase keys could not be fetched"),
    },
)
async def exchange(
    body: ExchangeIn,
    session: DbSession,
    settings: SettingsDep,
    verifier: FirebaseVerifier,
) -> ExchangeOut:
    if body.dev_phone is not None:
        if not settings.dev_login_enabled:
            raise ProblemError(
                403, "auth.dev_login_disabled", "Dev login works only on a local backend."
            )
        phone = body.dev_phone
    else:
        if verifier is None:
            raise ProblemError(
                503, "auth.provider_unavailable", "Phone sign-in is not configured here."
            )
        assert body.firebase_id_token is not None  # guaranteed by ExchangeIn
        phone = (await verifier.verify(body.firebase_id_token)).phone_e164

    user, created = await service.find_or_create_user(session, phone)
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
    )
    await session.commit()
    return ExchangeOut(
        access_token=issued.access_token,
        access_expires_in=issued.access_expires_in,
        refresh_token=issued.refresh_token,
        is_new_user=created,
        user=UserOut.model_validate(user),
    )


@router.post(
    "/auth/refresh",
    summary="Exchange a refresh token for new tokens (the old refresh token stops working)",
    responses={
        401: _problem("auth.invalid_token, or auth.refresh_reused (session ended)"),
    },
)
async def refresh(body: RefreshIn, session: DbSession, settings: SettingsDep) -> TokensOut:
    issued = await service.rotate(session, settings, body.refresh_token)
    await session.commit()
    return TokensOut(
        access_token=issued.access_token,
        access_expires_in=issued.access_expires_in,
        refresh_token=issued.refresh_token,
    )


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign this device out",
    responses={401: _problem("auth.invalid_token")},
)
async def logout(principal: CurrentPrincipal, session: DbSession) -> None:
    await service.revoke_session(session, principal.session_id)
    await session.commit()


@router.post(
    "/auth/logout-all",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out on every device",
    responses={401: _problem("auth.invalid_token")},
)
async def logout_all(principal: CurrentPrincipal, session: DbSession) -> None:
    await service.revoke_all_sessions(session, principal.user_id)
    await session.commit()


async def _signed_in_user(principal: CurrentPrincipal, session: DbSession) -> User:
    """The caller's account, refusing users who are blocked or whose device was signed out."""
    user = await session.get(User, principal.user_id)
    login = await session.get(LoginSession, principal.session_id)
    if user is None or user.status != "active" or login is None or login.revoked_at is not None:
        raise invalid_token()
    return user


SignedInUser = Annotated[User, Depends(_signed_in_user)]


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
async def sign_out_device(device_id: uuid.UUID, user: SignedInUser, session: DbSession) -> None:
    if not await service.sign_out_device(session, user.id, device_id):
        raise ProblemError(404, "identity.device_not_found", "No such signed-in device.")
    await session.commit()


@router.post(
    "/admin/user-roles",
    status_code=status.HTTP_201_CREATED,
    summary="Admin: give a user a staff role (Idempotency-Key required; audit-logged)",
    responses={
        403: _problem("auth.forbidden"),
        404: _problem("identity.user_not_found"),
        409: _problem("identity.role_exists"),
        422: _problem("identity.invalid_scope, request.idempotency_mismatch, request.invalid"),
    },
)
async def grant_role(
    body: RoleGrantIn,
    permit: Annotated[Permit, require("roles:grant")],
    session: DbSession,
    idem: Idempotent,
) -> RoleGrantOut:
    grant = await service.grant_role(
        session,
        phone_e164=body.phone_e164,
        role=body.role,
        scope_id=body.scope_id,
        granted_by=permit.principal.user_id,
    )
    result = RoleGrantOut.model_validate(grant)
    await idem.save(201, result)
    await session.commit()
    return result
