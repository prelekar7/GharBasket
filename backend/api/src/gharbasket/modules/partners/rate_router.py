"""HTTP endpoints of M8 for the rate card (spec §3.6): hub staff read their hub's, an admin sets
it."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Response

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.partners import rates
from gharbasket.modules.partners.kitchens import today
from gharbasket.modules.partners.schemas import RateCardOut, RateOut, RatesIn
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["partners"])

CanRead = Annotated[Permit, require("partner_rates:read")]
CanWrite = Annotated[Permit, require("partner_rates:write")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _out(hub_id: uuid.UUID, card: list[rates.Rate]) -> RateCardOut:
    return RateCardOut(
        hub_id=hub_id,
        rates=[
            RateOut(
                kind=r.kind,  # pyright: ignore[reportArgumentType]
                basis=r.basis,  # pyright: ignore[reportArgumentType]
                amount_paise=r.amount_paise,
                valid_from=r.valid_from,
            )
            for r in card
        ],
    )


async def _ensure_hub(session: DbSession, hub_id: uuid.UUID) -> None:
    if await hub_ops_api.get_hub(session, hub_id) is None:
        raise ProblemError(404, "hub_ops.hub_not_found", "No such hub.")


@router.get(
    "/hubs/{hub_id}/partner-rates",
    summary="What each partner kind earns at the hub (hub staff, franchise owner, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("hub_ops.hub_not_found"),
    },
)
async def get_rates(
    hub_id: uuid.UUID, permit: CanRead, session: DbSession, response: Response
) -> RateCardOut:
    permit.ensure(hub_id)
    await _ensure_hub(session, hub_id)
    response.headers["Cache-Control"] = "no-store"
    return _out(hub_id, await rates.rate_card(session, hub_id, today()))


@router.put(
    "/hubs/{hub_id}/partner-rates",
    summary="Set rates from a date on (admin; today or later; audit-logged)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("partners.rate_invalid, request.invalid"),
    },
)
async def set_rates(
    hub_id: uuid.UUID, body: RatesIn, permit: CanWrite, session: DbSession
) -> RateCardOut:
    permit.ensure(hub_id)
    await _ensure_hub(session, hub_id)
    card = await rates.replace(
        session,
        hub_id,
        [rates.Rate(r.kind, r.basis, r.amount_paise, r.valid_from) for r in body.rates],
        by_user_id=permit.principal.user_id,
    )
    out = _out(hub_id, card)
    await session.commit()
    return out
