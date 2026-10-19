"""HTTP endpoints of M8 for know your cook and ratings (spec §3.5): the kitchen's public card, and
a household's rating of a delivered tiffin."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.partners import ratings
from gharbasket.modules.partners.kitchens import IST
from gharbasket.modules.partners.schemas import (
    HygieneOut,
    KitchenCardOut,
    RatingOut,
    TiffinRatingIn,
    TiffinRatingOut,
)
from gharbasket.modules.partners.service import not_found as partner_not_found
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["partners"])

CanRate = Annotated[Permit, require("ratings:write")]
# A kitchen's rating is shown from this many ratings on (§3.5).
SHOWN_FROM = 5


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _out(rating: Any) -> TiffinRatingOut:
    return TiffinRatingOut(
        order_id=rating.order_id,
        partner_id=rating.partner_id,
        score=rating.score,
        comment=rating.comment,
        created_at=rating.created_at,
    )


@router.get(
    "/partners/{partner_id}/card",
    summary="Know your cook: a kitchen's public card (any signed-in user)",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("partners.partner_not_found"),
    },
)
async def kitchen_card(
    partner_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, response: Response
) -> KitchenCardOut:
    found = await ratings.card_rows(session, partner_id)
    if found is None:
        raise partner_not_found()
    partner, kitchen = found
    response.headers["Cache-Control"] = "no-store"
    rating = None
    if partner.rating_avg is not None and partner.rating_count >= SHOWN_FROM:
        rating = RatingOut(average=float(partner.rating_avg), count=partner.rating_count)
    hygiene = None
    if kitchen.hygiene_grade is not None and kitchen.inspected_on is not None:
        hygiene = HygieneOut(
            grade=kitchen.hygiene_grade,  # pyright: ignore[reportArgumentType]
            inspected_on=kitchen.inspected_on,
        )
    return KitchenCardOut(
        partner_id=partner.id,
        display_name=partner.display_name,
        photo_url=None,
        area=kitchen.area,
        since=partner.verified_at.astimezone(IST).strftime("%Y-%m"),
        fssai_reg_no=kitchen.fssai_reg_no,
        fssai_valid_until=kitchen.fssai_valid_until,
        hygiene=hygiene,
        rating=rating,
        skills=list(kitchen.skills),
        portions_served=await ratings.portions_served(session, partner.id),
    )


@router.post(
    "/ratings",
    status_code=201,
    summary="Rate a delivered tiffin, once, until 48 hours after delivery (Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.rating_not_found"),
        409: _problem("partners.rating_window_closed, partners.rating_exists"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def rate_tiffin(
    body: TiffinRatingIn, permit: CanRate, session: DbSession, idem: Idempotent
) -> TiffinRatingOut:
    rating = await ratings.rate(
        session,
        user_id=permit.principal.user_id,
        order_id=body.order_id,
        score=body.score,
        comment=body.comment,
    )
    out = _out(rating)
    await idem.save(201, out)
    await session.commit()
    return out


@router.get(
    "/ratings",
    summary="The rating you gave an order",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.rating_not_found"),
    },
)
async def my_rating(
    order_id: Annotated[uuid.UUID, Query()],
    permit: CanRate,
    session: DbSession,
    response: Response,
) -> TiffinRatingOut:
    response.headers["Cache-Control"] = "no-store"
    return _out(await ratings.of_order(session, permit.principal.user_id, order_id))
