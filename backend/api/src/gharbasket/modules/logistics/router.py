"""HTTP endpoints of M7 Logistics: vehicles (spec §3.1), route plans (§3.2) and rider execution
(§3.3)."""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import jobs, plans, service, views
from gharbasket.modules.logistics import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.logistics import routes as route_ops
from gharbasket.modules.logistics.models import Route, RoutePlan, Vehicle
from gharbasket.modules.logistics.schemas import (
    PlanKind,
    PlanSummary,
    PublishIn,
    RiderRef,
    RouteDetail,
    RoutePatch,
    RoutePlanOut,
    RoutePlansOut,
    RoutePlanSummary,
    RoutesOut,
    SolveIn,
    UserRef,
    VehicleIn,
    VehicleKind,
    VehicleOut,
    VehiclePatch,
    VehiclesOut,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import (
    PERMISSIONS,
    Permit,
    active_grants,
    forbidden,
    require,
)
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["logistics"])

CanReadVehicles = Annotated[Permit, require("vehicles:read")]
CanWriteVehicles = Annotated[Permit, require("vehicles:write")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


SIGNED_IN: dict[int | str, dict[str, Any]] = {
    401: _problem("auth.invalid_token"),
    403: _problem("auth.forbidden"),
}
CREATES: dict[int | str, dict[str, Any]] = {
    **SIGNED_IN,
    400: _problem("request.idempotency_key_required"),
}
EDITS: dict[int | str, dict[str, Any]] = {
    **SIGNED_IN,
    412: _problem("request.version_mismatch: reload and try again"),
    428: _problem("request.precondition_required"),
}
VEHICLE_RULES = "logistics.capacity_invalid, logistics.rider_invalid, request.invalid"


async def _vehicles_out(session: AsyncSession, vehicles: list[Vehicle]) -> list[VehicleOut]:
    names = await service.rider_names(session, vehicles)
    return [
        VehicleOut(
            id=v.id,
            hub_id=v.hub_id,
            label=v.label,
            kind=v.kind,  # pyright: ignore[reportArgumentType]
            registration=v.registration,
            capacity_kg=v.capacity_kg,
            capacity_crates=v.capacity_crates,
            capacity_tiffins=v.capacity_tiffins,
            default_rider=None
            if v.default_rider_user_id is None
            else RiderRef(user_id=v.default_rider_user_id, name=names.get(v.default_rider_user_id)),
            active=v.active,
            version=v.version,
            created_at=v.created_at,
        )
        for v in vehicles
    ]


@router.get(
    "/hubs/{hub_id}/vehicles",
    summary="A hub's vehicles, by label",
    responses={**SIGNED_IN, 404: _problem("hub_ops.hub_not_found")},
)
async def list_vehicles(
    hub_id: uuid.UUID,
    permit: CanReadVehicles,
    session: DbSession,
    response: Response,
    active: Annotated[bool | None, Query(description="Only active (or inactive) vehicles")] = None,
    kind: Annotated[VehicleKind | None, Query(description="Only this kind")] = None,
) -> VehiclesOut:
    permit.ensure(hub_id)
    await service.ensure_hub(session, hub_id)
    vehicles = await service.list_vehicles(session, hub_id, active, kind)
    response.headers["Cache-Control"] = "no-store"
    return VehiclesOut(items=await _vehicles_out(session, vehicles))


@router.post(
    "/hubs/{hub_id}/vehicles",
    status_code=status.HTTP_201_CREATED,
    summary="Add a vehicle to a hub (Idempotency-Key required)",
    responses={
        **CREATES,
        404: _problem("hub_ops.hub_not_found"),
        409: _problem("logistics.vehicle_label_taken"),
        422: _problem(f"{VEHICLE_RULES}, request.idempotency_mismatch"),
    },
)
async def create_vehicle(
    hub_id: uuid.UUID,
    body: VehicleIn,
    permit: CanWriteVehicles,
    session: DbSession,
    idem: Idempotent,
    response: Response,
) -> VehicleOut:
    permit.ensure(hub_id)
    vehicle = await service.create_vehicle(session, hub_id, body)
    [out] = await _vehicles_out(session, [vehicle])
    await idem.save(201, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.patch(
    "/vehicles/{vehicle_id}",
    summary="Change or deactivate a vehicle (If-Match: the vehicle's version)",
    description="Vehicles are deactivated, never deleted. A change applies to the next solve, "
    "never to a route already published. A vehicle of a hub outside the caller's roles answers "
    "404 logistics.vehicle_not_found, as if it did not exist (as M6's vendors do).",
    responses={
        **EDITS,
        404: _problem("logistics.vehicle_not_found"),
        409: _problem("logistics.vehicle_label_taken"),
        422: _problem(VEHICLE_RULES),
    },
)
async def patch_vehicle(
    vehicle_id: uuid.UUID,
    body: VehiclePatch,
    permit: CanWriteVehicles,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> VehicleOut:
    vehicle = await service.get_vehicle(session, vehicle_id)
    if not permit.allows(vehicle.hub_id):
        raise service.vehicle_not_found()
    vehicle = await service.update_vehicle(session, vehicle, body, expected)
    [out] = await _vehicles_out(session, [vehicle])
    await session.commit()
    set_etag(response, out.version)
    return out


# --- route plans (§3.2) -----------------------------------------------------------------------

CanSolve = Annotated[Permit, require("routes:write")]
CanReadPlans = Annotated[Permit, require("route_plans:read")]
Day = Annotated[date | None, Query(alias="date", description="IST date; default today")]
IST = ZoneInfo("Asia/Kolkata")
PLAN_RESPONSES: dict[int | str, dict[str, Any]] = {404: _problem("logistics.plan_not_found")}


def _within(day: date, earliest: int, latest: int) -> date:
    """`day` if it is between today + `earliest` and today + `latest` days (IST)."""
    today = datetime.now(IST).date()
    if not today + timedelta(days=earliest) <= day <= today + timedelta(days=latest):
        raise ProblemError(
            422,
            "logistics.date_out_of_range",
            "That date is outside the range allowed here.",
            extra={
                "earliest": (today + timedelta(days=earliest)).isoformat(),
                "latest": (today + timedelta(days=latest)).isoformat(),
            },
        )
    return day


async def _plans_out(
    session: AsyncSession, found: list[RoutePlan], *, with_routes: bool
) -> list[RoutePlanOut]:
    rows = {p.id: await plans.routes_of(session, p.id) for p in found} if with_routes else {}
    people = {p.requested_by for p in found} | {
        r.route.rider_user_id for routes in rows.values() for r in routes
    }
    names = await identity_api.user_names(session, sorted(u for u in people if u is not None))

    def person(user_id: uuid.UUID | None, model: type[RiderRef] | type[UserRef]) -> Any:
        return None if user_id is None else model(user_id=user_id, name=names.get(user_id))

    return [
        RoutePlanOut(
            id=p.id,
            hub_id=p.hub_id,
            delivery_date=p.delivery_date,
            kind=p.kind,  # pyright: ignore[reportArgumentType]
            status=p.status,  # pyright: ignore[reportArgumentType]
            trigger=p.trigger,  # pyright: ignore[reportArgumentType]
            source=p.source,  # pyright: ignore[reportArgumentType]
            distance_source=p.distance_source,  # pyright: ignore[reportArgumentType]
            model=p.model,
            summary=None if p.summary is None else PlanSummary.model_validate(p.summary),
            seconds=p.seconds,
            error=p.error,
            requested_by=person(p.requested_by, UserRef),
            created_at=p.created_at,
            published_at=p.published_at,
            routes=[views.route_summary(r, names) for r in rows.get(p.id, [])],
        )
        for p in found
    ]


async def _plan_in_scope(session: AsyncSession, permit: Permit, plan_id: uuid.UUID) -> RoutePlan:
    plan = await plans.get_plan(session, plan_id)
    if not permit.allows(plan.hub_id):
        raise plans.plan_not_found()
    return plan


@router.post(
    "/hubs/{hub_id}/route-plans",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solve (or re-solve) a hub's routes for today or tomorrow (Idempotency-Key required)",
    description="Answers at once with the queued plan; the solve runs in the background (the ML "
    "solver, or M7's fallback when it is down). Poll GET /v1/route-plans/{id}. A manual plan is "
    "never published automatically.",
    responses={
        **CREATES,
        404: _problem("hub_ops.hub_not_found"),
        409: _problem("logistics.solve_running, logistics.routes_started"),
        422: _problem("logistics.date_out_of_range, request.idempotency_mismatch, request.invalid"),
    },
)
async def solve_plan(
    hub_id: uuid.UUID,
    body: SolveIn,
    permit: CanSolve,
    session: DbSession,
    idem: Idempotent,
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> RoutePlanOut:
    permit.ensure(hub_id)
    await service.ensure_hub(session, hub_id)
    day = _within(body.delivery_date, 0, 1)
    plan = await plans.request_solve(
        session, hub_id, day, body.kind, trigger="manual", requested_by=permit.principal.user_id
    )
    [out] = await _plans_out(session, [plan], with_routes=True)
    await idem.save(202, out)
    await session.commit()
    state = request.app.state
    jobs.solve_soon(
        getattr(state, "sessionmaker", None), getattr(state, "http", None), settings, plan.id
    )
    return out


@router.get(
    "/hubs/{hub_id}/route-plans",
    summary="A hub's route plans for a date, newest first (without routes)",
    responses={
        **SIGNED_IN,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("logistics.date_out_of_range"),
    },
)
async def list_plans(
    hub_id: uuid.UUID,
    permit: CanReadPlans,
    session: DbSession,
    response: Response,
    day: Day = None,
    kind: Annotated[PlanKind | None, Query(description="Only this kind")] = None,
) -> RoutePlansOut:
    permit.ensure(hub_id)
    await service.ensure_hub(session, hub_id)
    found = await plans.list_plans(
        session, hub_id, _within(day or datetime.now(IST).date(), -7, 1), kind
    )
    response.headers["Cache-Control"] = "no-store"
    out = await _plans_out(session, found, with_routes=False)
    return RoutePlansOut(
        items=[RoutePlanSummary.model_validate(p, from_attributes=True) for p in out]
    )


@router.get(
    "/route-plans/{plan_id}",
    summary="A route plan with its routes (poll it after a solve)",
    responses={**SIGNED_IN, **PLAN_RESPONSES},
)
async def get_plan(
    plan_id: uuid.UUID, permit: CanReadPlans, session: DbSession, response: Response
) -> RoutePlanOut:
    plan = await _plan_in_scope(session, permit, plan_id)
    [out] = await _plans_out(session, [plan], with_routes=True)
    response.headers["Cache-Control"] = "no-store"
    return out


@router.post(
    "/route-plans/{plan_id}/publish",
    summary="Publish a solved plan (Idempotency-Key required)",
    description="The routes become published; the date's previously published plan is "
    "superseded and its routes cancelled, unless one of them has started. A plan with problems "
    "needs accept_problems (audited); one that leaves a stop off every route never publishes.",
    responses={
        **CREATES,
        **PLAN_RESPONSES,
        409: _problem(
            "logistics.plan_not_publishable, logistics.plan_incomplete, "
            "logistics.plan_has_problems, logistics.routes_started"
        ),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def publish_plan(
    plan_id: uuid.UUID,
    permit: CanSolve,
    session: DbSession,
    idem: Idempotent,
    body: Annotated[PublishIn | None, Body()] = None,
) -> RoutePlanOut:
    plan = await _plan_in_scope(session, permit, plan_id)
    accept = body is not None and body.accept_problems
    await plans.publish(session, plan, user_id=permit.principal.user_id, accept_problems=accept)
    [out] = await _plans_out(session, [plan], with_routes=True)
    await idem.save(200, out)
    await session.commit()
    return out


# --- rider execution (§3.3) -------------------------------------------------------------------


def require_any(*permissions: str) -> Any:
    """Like `require`, for an endpoint two permissions open (hub staff's and the rider's)."""

    async def check(
        principal: CurrentPrincipal,
        x_active_role: Annotated[str | None, Header(max_length=100)] = None,
    ) -> Permit:
        roles = set().union(*(PERMISSIONS.roles_for(p) for p in permissions))
        grants = tuple(g for g in active_grants(principal, x_active_role) if g.role in roles)
        if not grants:
            raise forbidden()
        return Permit(principal, "|".join(permissions), grants)

    return Depends(check)


CanReadRoutes = Annotated[Permit, require("routes:read")]
CanDrive = Annotated[Permit, require("routes:execute")]
RouteReader = Annotated[Permit, require_any("routes:read", "routes:execute")]
RouteStatusQ = Literal["draft", "published", "in_progress", "completed", "cancelled"]
ROUTE_404: dict[int | str, dict[str, Any]] = {404: _problem("logistics.route_not_found")}


async def _detail(
    session: AsyncSession, route: Route, response: Response | None = None
) -> RouteDetail:
    out = await views.route_detail(session, route)
    if response is not None:
        set_etag(response, route.version)
        response.headers["Cache-Control"] = "no-store"
    return out


@router.get(
    "/hubs/{hub_id}/routes",
    summary="A hub's routes for a date, by departure and vehicle",
    responses={
        **SIGNED_IN,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("logistics.date_out_of_range"),
    },
)
async def list_routes(
    hub_id: uuid.UUID,
    permit: CanReadRoutes,
    session: DbSession,
    response: Response,
    day: Day = None,
    kind: Annotated[PlanKind | None, Query(description="Only this kind")] = None,
    route_status: Annotated[RouteStatusQ | None, Query(alias="status")] = None,
) -> RoutesOut:
    permit.ensure(hub_id)
    await service.ensure_hub(session, hub_id)
    on = _within(day or datetime.now(IST).date(), -7, 1)
    rows = await route_ops.hub_routes(session, hub_id, on, kind, route_status)
    response.headers["Cache-Control"] = "no-store"
    return RoutesOut(items=await views.summaries(session, rows))


@router.get(
    "/riders/me/routes",
    summary="The caller's published, in-progress and completed routes for today or tomorrow",
    responses={**SIGNED_IN, 422: _problem("logistics.date_out_of_range")},
)
async def my_routes(
    permit: CanDrive, session: DbSession, response: Response, day: Day = None
) -> RoutesOut:
    rows = await route_ops.rider_routes(
        session, permit, _within(day or datetime.now(IST).date(), 0, 1)
    )
    response.headers["Cache-Control"] = "no-store"
    return RoutesOut(items=await views.summaries(session, rows))


@router.get(
    "/routes/{route_id}",
    summary="A route with its stops (ETag: the route's version)",
    description="Hub staff of its hub, or its rider (published routes on). The handover codes "
    "are shown only while the route is in progress.",
    responses={**SIGNED_IN, **ROUTE_404},
)
async def get_route(
    route_id: uuid.UUID, permit: RouteReader, session: DbSession, response: Response
) -> RouteDetail:
    route, _ = await route_ops.get_route(session, route_id, permit)
    return await _detail(session, route, response)


@router.patch(
    "/routes/{route_id}",
    summary="Change a route's rider or vehicle before it starts (If-Match: the route's version)",
    description="A new rider gets logistics.route_published again (with previous_rider_user_id).",
    responses={
        **EDITS,
        **ROUTE_404,
        409: _problem("logistics.routes_started, logistics.route_closed"),
        422: _problem("logistics.rider_invalid, logistics.vehicle_too_small, request.invalid"),
    },
)
async def patch_route(
    route_id: uuid.UUID,
    body: RoutePatch,
    permit: CanSolve,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> RouteDetail:
    route, who = await route_ops.get_route(session, route_id, permit, for_update=True)
    await route_ops.edit(session, route, who, body, expected)
    out = await _detail(session, route, response)
    await session.commit()
    return out
