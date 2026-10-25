"""HTTP endpoints of M12 Console & Analytics: the AI health table, the hub's today board, KPIs,
the societies table, unit economics, the P&L and the hub's assumptions (impact follows)."""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import (
    assumptions,
    economics,
    kpis,
    reads,
    service,
    societies,
    today,
)
from gharbasket.modules.analytics import permissions as _permissions  # noqa: F401
from gharbasket.modules.analytics.economics_schemas import (
    AssumptionsIn,
    HubAssumptionsOut,
    PnlOut,
    UnitEconomicsOut,
)
from gharbasket.modules.analytics.schemas import (
    AiHealthOut,
    Grain,
    KpiSource,
    KpisOut,
    SocietiesOut,
    TodayOut,
)
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.locale import Lang
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.valkey import Valkey

router = APIRouter(tags=["analytics"])

CanRead = Annotated[Permit, require("analytics:read")]
CanSeeAiHealth = Annotated[Permit, require("analytics:ai_health")]
# Calendar dates the console can ask for; the day arithmetic around them never leaves the range.
CalendarDate = Annotated[date, Field(ge=date(2000, 1, 1), le=date(2100, 12, 31))]
REQUESTS_PER_MINUTE = 60  # per user, on /v1/analytics/* (spec §3.9)


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


async def _limited(principal: CurrentPrincipal, valkey: Valkey) -> None:
    await rate_limit.enforce(
        valkey,
        f"analytics:user:{principal.user_id}",
        limit=REQUESTS_PER_MINUTE,
        window_s=60,
        code="request.rate_limited",
        detail="Too many requests. Wait a little and try again.",
    )


Limited = Depends(_limited)
READ_ERRORS: dict[int | str, dict[str, Any]] = {
    403: _problem("auth.forbidden (a hub outside your grants; the hub left out by a non-admin)"),
    429: _problem("request.rate_limited (60 a minute per user; see Retry-After)"),
    503: _problem("analytics.timeout (a read over 3 s)"),
}


@router.get(
    "/analytics/ai-health",
    summary="Every model's newest metrics, per-hub drift and the ML-or-fallback share (admin)",
)
async def ai_health(permit: CanSeeAiHealth, session: DbSession, response: Response) -> AiHealthOut:
    response.headers["Cache-Control"] = "no-store"
    return await service.ai_health(session)


@router.get(
    "/analytics/hubs/{hub_id}/today",
    summary="The hub's today board: live counts, the day's steps and what needs attention "
    "(hub manager, franchise owner, admin)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("analytics.range_invalid (a date before today - 7 or after today + 1)"),
    },
)
async def hub_today(
    hub_id: uuid.UUID,
    permit: CanRead,
    session: DbSession,
    response: Response,
    lang: Lang,
    on: Annotated[
        CalendarDate | None,
        Query(
            alias="date",
            description="An IST delivery date from today - 7 to today + 1; default today",
        ),
    ] = None,
) -> TodayOut:
    permit.ensure(hub_id)
    response.headers["Cache-Control"] = "no-store"
    today_ist = reads.ist_today()
    day = on or today_ist
    if not today.date_in_range(day, today_ist):
        raise ProblemError(
            422,
            "analytics.range_invalid",
            "The board answers dates from 7 days ago to tomorrow.",
        )
    async with reads.read_limit(session):
        return await today.board(session, hub_id, day, lang, reads.now())


@router.get(
    "/analytics/kpis",
    summary="KPI series per day or ISO week, from the daily view (hub manager, franchise owner, "
    "admin; only an admin may leave the hub out)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        422: _problem(
            "analytics.data_source_required · analytics.range_invalid · analytics.range_too_long"
        ),
    },
)
async def kpi_series(
    permit: CanRead,
    session: DbSession,
    response: Response,
    hub_id: Annotated[
        uuid.UUID | None, Query(description="Left out: every hub (admin only)")
    ] = None,
    from_: Annotated[
        CalendarDate | None,
        Query(alias="from", description="An IST date; default the start of the last 12 weeks"),
    ] = None,
    to: Annotated[CalendarDate | None, Query(description="An IST date; default today")] = None,
    grain: Annotated[Grain, Query(description="day (≤ 92 days) or week (≤ 104 weeks)")] = "week",
    data_source: Annotated[
        KpiSource | None,
        Query(description="Required: `real` or `synthetic`, never pooled"),
    ] = None,
) -> KpisOut:
    reads.ensure_scope(permit, hub_id)
    if data_source is None:
        raise ProblemError(
            422, "analytics.data_source_required", "Say which data: `real` or `synthetic`."
        )
    start, end = kpis.resolve_range(from_, to, grain, reads.ist_today())
    response.headers["Cache-Control"] = "private, max-age=60"
    return await kpis.kpis(session, hub_id, data_source, start, end, grain)


@router.get(
    "/analytics/societies",
    summary="Each society's unlock progress and the last 4 weeks' households, approval and box "
    "value (hub manager, franchise owner, admin; only an admin may leave the hub out)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        422: _problem("analytics.data_source_required"),
    },
)
async def society_table(
    permit: CanRead,
    session: DbSession,
    response: Response,
    hub_id: Annotated[
        uuid.UUID | None, Query(description="Left out: every hub (admin only)")
    ] = None,
    data_source: Annotated[
        KpiSource | None,
        Query(description="Required: `real` or `synthetic`, never pooled"),
    ] = None,
) -> SocietiesOut:
    reads.ensure_scope(permit, hub_id)
    if data_source is None:
        raise ProblemError(
            422, "analytics.data_source_required", "Say which data: `real` or `synthetic`."
        )
    response.headers["Cache-Control"] = "private, max-age=60"
    return await societies.societies(session, hub_id, data_source, reads.ist_today())


# --- unit economics, the P&L and the hub's assumptions (spec §3.4) --------------------------------

CanSeeEconomics = Annotated[Permit, require("analytics:economics")]
CanWriteAssumptions = Annotated[Permit, require("analytics:assumptions_write")]
# Reads of the economics are never cached: the console saves assumptions and reads the numbers back
# at once, and a stale ETag would turn the next save into a 412 for up to a minute.
NO_CACHE = "private, no-cache"
_ETAG = {"ETag": {"description": 'The assumptions version, e.g. "3"; send it back in If-Match'}}


def _override(description: str) -> Any:
    return Query(description=description)


async def _overrides(
    aov_paise: Annotated[int | None, _override("What-if: average box value (paise)")] = None,
    margin_bp: Annotated[int | None, _override("What-if: margin (basis points)")] = None,
    delivery_paise: Annotated[int | None, _override("What-if: delivery cost per box")] = None,
    packing_paise: Annotated[int | None, _override("What-if: packing cost per box")] = None,
    captain_paise: Annotated[int | None, _override("What-if: captain pay per box")] = None,
    payment_fee_bp: Annotated[int | None, _override("What-if: payment fee (basis points)")] = None,
    cac_paise: Annotated[int | None, _override("What-if: acquisition cost per box")] = None,
    delivery_fee_paise: Annotated[
        int | None, _override("What-if: the pre-live delivery fee per box")
    ] = None,
) -> dict[str, int | None]:
    values = {
        "aov_paise": aov_paise,
        "margin_bp": margin_bp,
        "delivery_paise": delivery_paise,
        "packing_paise": packing_paise,
        "captain_paise": captain_paise,
        "payment_fee_bp": payment_fee_bp,
        "cac_paise": cac_paise,
        "delivery_fee_paise": delivery_fee_paise,
    }
    return values


async def _economics_hub(
    session: AsyncSession, permit: Permit, hub_id: uuid.UUID | None
) -> uuid.UUID:
    """The hub of an economics read: within the caller's grants and existing. Assumptions are a
    hub's own, so even an admin names the hub."""
    reads.ensure_scope(permit, hub_id)
    if hub_id is None:
        raise ProblemError(422, "request.invalid", "Give the hub you want to read (`hub_id`).")
    await _hub_exists(session, hub_id)
    return hub_id


async def _hub_exists(session: AsyncSession, hub_id: uuid.UUID) -> None:
    if await hub_ops_api.get_hub(session, hub_id) is None:
        raise ProblemError(404, "hub_ops.hub_not_found", "No such hub.")


@router.get(
    "/analytics/unit-economics",
    summary="A box's contribution from measured inputs, saved assumptions and what-if overrides "
    "(franchise owner, admin)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem(
            "analytics.data_source_required · analytics.assumptions_invalid (an override out of "
            "range, with errors[]) · analytics.range_invalid · analytics.range_too_long"
        ),
    },
)
async def unit_economics(
    permit: CanSeeEconomics,
    session: DbSession,
    response: Response,
    overrides: Annotated[dict[str, int | None], Depends(_overrides)],
    hub_id: Annotated[uuid.UUID | None, Query(description="The hub (required)")] = None,
    from_: Annotated[
        CalendarDate | None,
        Query(alias="from", description="An IST date; moves back to its Monday. Default: 12 weeks"),
    ] = None,
    to: Annotated[
        CalendarDate | None, Query(description="An IST date; moves on to its Sunday. Default today")
    ] = None,
    data_source: Annotated[
        KpiSource | None, Query(description="Required: `real` or `synthetic`, never pooled")
    ] = None,
    prefer: Annotated[
        Literal["measured", "assumed"],
        Query(description="`measured`: use what was measured; `assumed`: only the saved values"),
    ] = "measured",
) -> UnitEconomicsOut:
    hub = await _economics_hub(session, permit, hub_id)
    if data_source is None:
        raise ProblemError(
            422, "analytics.data_source_required", "Say which data: `real` or `synthetic`."
        )
    assumptions.ensure_valid(overrides)
    response.headers["Cache-Control"] = NO_CACHE
    return await economics.unit_economics(
        session,
        hub,
        data_source,
        from_,
        to,
        prefer=prefer,
        overrides=overrides,
        today=reads.ist_today(),
    )


@router.get(
    "/analytics/pnl",
    summary="A month's statement: revenue, costs and contribution, each line measured or assumed "
    "(franchise owner, admin)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem(
            "analytics.data_source_required · analytics.range_invalid (not a month, or one that "
            "has not begun) · analytics.range_too_long (over the last 24 months)"
        ),
    },
)
async def pnl(
    permit: CanSeeEconomics,
    session: DbSession,
    response: Response,
    period: Annotated[str, Query(description="The month, `YYYY-MM`", examples=["2027-02"])],
    hub_id: Annotated[uuid.UUID | None, Query(description="The hub (required)")] = None,
    data_source: Annotated[
        KpiSource | None, Query(description="Required: `real` or `synthetic`, never pooled")
    ] = None,
) -> PnlOut:
    hub = await _economics_hub(session, permit, hub_id)
    if data_source is None:
        raise ProblemError(
            422, "analytics.data_source_required", "Say which data: `real` or `synthetic`."
        )
    response.headers["Cache-Control"] = NO_CACHE
    return await economics.pnl(session, hub, data_source, period, reads.ist_today())


@router.get(
    "/analytics/hubs/{hub_id}/assumptions",
    summary="The hub's saved assumptions, their version (ETag) and the last 20 saves "
    "(franchise owner, admin)",
    dependencies=[Limited],
    responses={
        200: {"headers": _ETAG},
        403: READ_ERRORS[403],
        404: _problem("hub_ops.hub_not_found"),
        429: READ_ERRORS[429],
    },
)
async def hub_assumptions(
    hub_id: uuid.UUID, permit: CanSeeEconomics, session: DbSession, response: Response
) -> HubAssumptionsOut:
    permit.ensure(hub_id)
    await _hub_exists(session, hub_id)
    out = await assumptions.view(session, hub_id)
    response.headers["Cache-Control"] = NO_CACHE
    set_etag(response, out.version)
    return out


@router.put(
    "/analytics/hubs/{hub_id}/assumptions",
    summary="Save the hub's assumptions (all keys; If-Match: the version; audit-logged) "
    "(franchise owner, admin)",
    dependencies=[Limited],
    responses={
        200: {"headers": _ETAG},
        403: READ_ERRORS[403],
        404: _problem("hub_ops.hub_not_found"),
        412: _problem("request.precondition_failed (the version moved on; reload)"),
        422: _problem("analytics.assumptions_invalid (a value out of range, with errors[])"),
        428: _problem("request.precondition_required (send If-Match)"),
        429: READ_ERRORS[429],
    },
)
async def put_hub_assumptions(
    hub_id: uuid.UUID,
    body: AssumptionsIn,
    permit: CanWriteAssumptions,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> HubAssumptionsOut:
    permit.ensure(hub_id)
    await _hub_exists(session, hub_id)
    out = await assumptions.replace(
        session, hub_id, body.model_dump(), expected, permit.principal.user_id
    )
    await session.commit()
    response.headers["Cache-Control"] = NO_CACHE
    set_etag(response, out.version)
    return out
