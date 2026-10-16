"""HTTP endpoints for referrals (M9 §3.3): the caller's code and referrals, and the neighbour's
claim. No response ever names the neighbour."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Response

from gharbasket.modules.community import referrals
from gharbasket.modules.community.schemas import (
    ReferralClaimIn,
    ReferralClaimOut,
    ReferralItem,
    ReferralRewards,
    ReferralsOut,
    ReferralSummary,
    ReferredBy,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter()
AppSettings = Annotated[Settings, Depends(get_settings)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _paid(status: str, points: int) -> int | None:
    """Points show once converted (0 over the monthly cap); null before."""
    return points if status == "converted" else None


@router.get(
    "/referrals",
    summary="My referral code and link, what referrals earn, and the neighbours who joined",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
    },
)
async def my_referrals(
    permit: Annotated[Permit, require("referrals:read")],
    session: DbSession,
    settings: AppSettings,
    lang: Lang,
    response: Response,
) -> ReferralsOut:
    response.headers["Cache-Control"] = "no-store"
    user_id = permit.principal.user_id
    code = await referrals.code_for(session, user_id)  # made on the first visit
    link = await referrals.link_for(session, settings, user_id, code)
    mine = await referrals.referred(session, user_id)
    view = ReferralsOut(
        code=code,
        link=link,
        share_text=referrals.share_text(lang, code, link),
        rewards=ReferralRewards(
            referrer_points=settings.referral_points_referrer,
            referee_points=settings.referral_points_referee,
            monthly_cap=referrals.MONTHLY_CAP,
        ),
        summary=ReferralSummary(**await referrals.summary(session, user_id)),
        referred_by=None
        if mine is None
        else ReferredBy(status=mine.status, points=_paid(mine.status, mine.referee_points)),  # pyright: ignore[reportArgumentType]
        items=[
            ReferralItem(
                id=row.id,
                status=row.status,  # pyright: ignore[reportArgumentType]
                joined_on=row.joined_on,
                converted_on=row.converted_on,
                points=_paid(row.status, row.referrer_points),
            )
            for row in await referrals.made_by(session, user_id)
        ],
    )
    await session.commit()
    return view


@router.post(
    "/referrals",
    status_code=201,
    summary="Use a neighbour's referral code (Idempotency-Key required)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("community.referral_code_unknown"),
        409: _problem("community.already_referred, community.referral_window_closed"),
        422: _problem(
            "community.household_required, community.referral_self, "
            "request.idempotency_mismatch, request.invalid"
        ),
    },
)
async def claim_referral(
    body: ReferralClaimIn,
    permit: Annotated[Permit, require("referrals:claim")],
    session: DbSession,
    settings: AppSettings,
    idem: Idempotent,
) -> ReferralClaimOut:
    referral = await referrals.claim(session, settings, permit.principal.user_id, body.code)
    created = ReferralClaimOut(
        id=referral.id,
        status="joined",
        referee_points=referral.referee_points,
        converts_on="first_delivered_box",
    )
    await idem.save(201, created)
    await session.commit()
    return created
