"""HTTP endpoints of M12 Console & Analytics: the AI health table, the hub's today board, KPIs,
the societies table, cohorts and partner earnings (the unit-economics and impact views follow)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field

from gharbasket.modules.analytics import cohorts, earnings, kpis, reads, service, societies, today
from gharbasket.modules.analytics import permissions as _permissions  # noqa: F401
from gharbasket.modules.analytics.schemas import (
    AiHealthOut,
    CohortsOut,
    Grain,
    KpiSource,
    KpisOut,
    PartnerEarningsOut,
    PartnerKind,
    SocietiesOut,
    TodayOut,
)
from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Lang
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.valkey import Valkey

router = APIRouter(tags=["analytics"])

CanRead = Annotated[Permit, require("analytics:read")]
CanSeeCohorts = Annotated[Permit, require("analytics:cohorts")]
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


@router.get(
    "/analytics/cohorts",
    summary="Retention of each signup week's households, per data source (admin)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        422: _problem(
            "analytics.data_source_required · analytics.range_too_long (weeks over 26) · "
            "analytics.range_invalid (weeks under 4)"
        ),
    },
)
async def cohort_retention(
    permit: CanSeeCohorts,
    session: DbSession,
    response: Response,
    hub_id: Annotated[uuid.UUID | None, Query(description="Left out: every hub")] = None,
    data_source: Annotated[
        KpiSource | None,
        Query(description="Required: `real` or `synthetic`, never pooled"),
    ] = None,
    weeks: Annotated[
        int, Query(description="Signup weeks to show, and the weeks of retention: 4 to 26")
    ] = cohorts.DEFAULT_WEEKS,
) -> CohortsOut:
    reads.ensure_scope(permit, hub_id)
    if data_source is None:
        raise ProblemError(
            422, "analytics.data_source_required", "Say which data: `real` or `synthetic`."
        )
    cohorts.check_weeks(weeks)
    response.headers["Cache-Control"] = "private, max-age=60"
    return await cohorts.cohorts(session, hub_id, data_source, weeks, reads.ist_today())


@router.get(
    "/analytics/partner-earnings",
    summary="The weekly spread of what one kind of partner earned, never an individual (hub "
    "manager, franchise owner, admin; only an admin may leave the hub out)",
    dependencies=[Limited],
    responses={
        **READ_ERRORS,
        422: _problem(
            "analytics.data_source_required · analytics.range_invalid · analytics.range_too_long"
        ),
    },
)
async def partner_earnings(
    permit: CanRead,
    session: DbSession,
    response: Response,
    hub_id: Annotated[
        uuid.UUID | None, Query(description="Left out: every hub (admin only)")
    ] = None,
    kind: Annotated[PartnerKind, Query(description="Which partners")] = "kitchen",
    from_: Annotated[
        CalendarDate | None,
        Query(alias="from", description="An IST date; default the start of the last 12 weeks"),
    ] = None,
    to: Annotated[CalendarDate | None, Query(description="An IST date; default today")] = None,
    data_source: Annotated[
        KpiSource | None,
        Query(description="Required: `real` or `synthetic`, never pooled"),
    ] = None,
) -> PartnerEarningsOut:
    reads.ensure_scope(permit, hub_id)
    if data_source is None:
        raise ProblemError(
            422, "analytics.data_source_required", "Say which data: `real` or `synthetic`."
        )
    start, end = kpis.resolve_range(from_, to, "week", reads.ist_today())
    response.headers["Cache-Control"] = "private, max-age=60"
    return await earnings.partner_earnings(session, hub_id, kind, data_source, start, end)
