"""HTTP endpoint of M8 for earnings (spec §3.6): a partner's week, read back from M5's ledger."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.partners import earnings, service
from gharbasket.modules.partners.schemas import (
    EarningOut,
    EarningRefOut,
    EarningsDayOut,
    EarningsKindOut,
    EarningsWeekOut,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["partners"])

CanRead = Annotated[Permit, require("earnings:read")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


@router.get(
    "/earnings",
    summary="What you earned in a week, by day and kind, from the ledger (every kind you are)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("partners.week_invalid, request.invalid"),
    },
)
async def my_earnings(
    permit: CanRead,
    session: DbSession,
    response: Response,
    week: Annotated[str | None, Query(max_length=16, examples=["2027-W02"])] = None,
) -> EarningsWeekOut:
    response.headers["Cache-Control"] = "no-store"
    iso = earnings.parse_week(week)
    mine = await service.for_user(session, permit.principal.user_id)
    kinds = {p.id: p.kind for p in mine}
    found = await billing_api.partner_week(session, list(kinds), iso)
    by_kind: dict[tuple[str, str], list[int]] = {}
    for line in found.by_basis:
        total = by_kind.setdefault((kinds[line.partner_id], line.basis), [0, 0])
        total[0], total[1] = total[0] + line.units, total[1] + line.amount_paise
    return EarningsWeekOut.model_validate(
        {
            "week": iso,
            "from": found.start,
            "to": found.end,
            "total_paise": found.total_paise,
            "by_kind": [
                EarningsKindOut(kind=k, basis=b, units=u, amount_paise=a)  # pyright: ignore[reportArgumentType]
                for (k, b), (u, a) in sorted(by_kind.items())
            ],
            "days": [EarningsDayOut(date=d.day, amount_paise=d.amount_paise) for d in found.days],
            "entries": [
                EarningOut(
                    id=e.id,
                    kind=kinds[e.partner_id],  # pyright: ignore[reportArgumentType]
                    basis=e.basis,  # pyright: ignore[reportArgumentType]
                    units=e.units,
                    amount_paise=e.amount_paise,
                    ref=EarningRefOut(type="order", id=e.ref_id),  # pyright: ignore[reportArgumentType]
                    at=e.at,
                )
                for e in found.entries
                if e.ref_id is not None
            ],
            "payouts": found.payouts,
            "generated_at": datetime.now(UTC),
        }
    )
