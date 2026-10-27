"""HTTP endpoints of M12's impact numbers: the console's report, as JSON or CSV (spec §3.6), and the
public society page (§3.7)."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response

from gharbasket.modules.analytics import impact, public, reads
from gharbasket.modules.analytics.impact_schemas import ImpactOut, ImpactScope, PublicSocietyOut
from gharbasket.modules.analytics.schemas import KpiSource
from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import Valkey

router = APIRouter(tags=["analytics"])

CanSeeImpact = Annotated[Permit, require("analytics:impact")]
AppSettings = Annotated[Settings, Depends(get_settings)]
REQUESTS_PER_MINUTE = 60  # per user on /v1/analytics/*, shared with the other endpoints (§3.9)
PUBLIC_REQUESTS_PER_MINUTE = 60  # per IP on /v1/public/* (§3.9)
PUBLIC_CACHE = "public, max-age=300, stale-while-revalidate=600"


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


@router.get(
    "/analytics/impact",
    response_model=ImpactOut,
    summary="A month's impact for the platform, a hub or an NGO, as JSON or CSV (NGO coordinator "
    "for their NGO, hub manager and franchise owner for their hub, admin)",
    dependencies=[Depends(_limited)],
    responses={
        200: {
            "description": "The report as JSON, or with `format=csv` one row per (month, hub, ngo)",
            "content": {"text/csv": {"schema": {"type": "string"}}},
        },
        403: _problem(
            "auth.forbidden (a scope, hub, NGO or data source outside your grants; the hub or "
            "NGO left out by a non-admin)"
        ),
        404: _problem("community.ngo_not_found (an admin's unknown NGO)"),
        422: _problem(
            "request.invalid (hub_id or ngo_id left out by an admin) · analytics.range_invalid "
            "(a future month) · analytics.range_too_long (older than 24 months)"
        ),
        429: _problem("request.rate_limited (60 a minute per user; see Retry-After)"),
        503: _problem("analytics.timeout (a read over 3 s)"),
    },
)
async def impact_report(
    permit: CanSeeImpact,
    session: DbSession,
    response: Response,
    scope: Annotated[ImpactScope, Query(description="platform (admin) | hub | ngo")],
    hub_id: Annotated[uuid.UUID | None, Query(description="Scope hub: the hub")] = None,
    ngo_id: Annotated[uuid.UUID | None, Query(description="Scope ngo: the NGO")] = None,
    month: Annotated[
        str | None,
        Query(
            pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
            description="An IST month, this one by default, within the last 24",
            examples=["2027-02"],
        ),
    ] = None,
    fmt: Annotated[
        Literal["json", "csv"], Query(alias="format", description="csv: an attachment")
    ] = "json",
    data_source: Annotated[
        KpiSource, Query(description="`synthetic` is for admins (demos)")
    ] = "real",
) -> ImpactOut | Response:
    target = await impact.resolve(session, permit, scope, hub_id, ngo_id, data_source)
    first = impact.resolve_month(month, reads.ist_today())
    if fmt == "csv":
        loaded, _ = await impact.load(session, target, data_source, first)
        return Response(
            impact.render_csv(loaded),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{impact.filename(target, first)}"',
                "Cache-Control": "private, max-age=60",
            },
        )
    response.headers["Cache-Control"] = "private, max-age=60"
    return await impact.report(session, target, data_source, first)


def _client_ip(request: Request) -> str:
    # On Render, uvicorn runs with --proxy-headers, so this is the real caller's address.
    return request.client.host if request.client else "unknown"


@router.get(
    "/public/societies/{slug}",
    response_model=PublicSocietyOut,
    summary="A society's landing page: unlock progress and aggregates only (no sign-in)",
    responses={
        404: _problem("community.society_not_found (unknown, prospect or paused)"),
        429: _problem("request.rate_limited (60 a minute per IP; see Retry-After)"),
    },
)
async def public_society(
    request: Request,
    response: Response,
    session: DbSession,
    valkey: Valkey,
    settings: AppSettings,
    slug: Annotated[str, Path(min_length=1, max_length=80, examples=["sunrise-towers-kharghar"])],
) -> PublicSocietyOut:
    await rate_limit.enforce(
        valkey,
        f"public:ip:{_client_ip(request)}",
        limit=PUBLIC_REQUESTS_PER_MINUTE,
        window_s=60,
        code="request.rate_limited",
        detail="Too many requests. Wait a little and try again.",
    )
    page = await public.page(session, settings, slug)
    if page is None:
        raise ProblemError(404, "community.society_not_found", "No such society.")
    response.headers["Cache-Control"] = PUBLIC_CACHE
    return page
