"""HTTP endpoints of M1 Identity & Household: sign-in, tokens and the signed-in user.

Sign-in is phone OTP: the app verifies the OTP with Firebase and sends the resulting ID token to
`POST /v1/auth/exchange`. The first exchange for a phone number creates the account.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select

from gharbasket.modules.identity import service
from gharbasket.modules.identity.firebase import FirebaseTokenVerifier
from gharbasket.modules.identity.models import LoginSession, User, UserRole
from gharbasket.modules.identity.schemas import (
    ExchangeIn,
    ExchangeOut,
    MeOut,
    RefreshIn,
    RoleOut,
    TokensOut,
    UserOut,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
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


@router.get(
    "/me",
    summary="The signed-in user and all their roles",
    responses={401: _problem("auth.invalid_token")},
)
async def me(principal: CurrentPrincipal, session: DbSession) -> MeOut:
    user = await session.get(User, principal.user_id)
    login = await session.get(LoginSession, principal.session_id)
    if user is None or user.status != "active" or login is None or login.revoked_at is not None:
        raise invalid_token()
    roles = await session.execute(
        select(UserRole).where(UserRole.user_id == user.id).order_by(UserRole.created_at)
    )
    return MeOut(
        **UserOut.model_validate(user).model_dump(),
        roles=[RoleOut.model_validate(role) for role in roles.scalars()],
    )
