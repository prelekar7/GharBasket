"""HTTP endpoints of M7 Logistics: vehicles (spec §3.1), route plans (§3.2), rider execution
(§3.3), the handover and doors (§3.4), the delivery card (§3.5) and return-leg pickups (§3.6)."""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response, status
from fastapi.sse import EventSourceResponse
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import (
    attempts,
    card,
    deliveries,
    execution,
    handover,
    jobs,
    live,
    pickups,
    plans,
    service,
    tiffins,
    tracking,
    views,
)
from gharbasket.modules.logistics import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.logistics import routes as route_ops
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.modules.logistics.schemas import (
    CaptainDay,
    CaptainStop,
    CollectIn,
    CollectOut,
    CompleteIn,
    DeliveriesPage,
    DeliveryCard,
    DeliveryOut,
    DeliveryStatus,
    Expected,
    FieldTap,
    FlatCounts,
    FlatsOut,
    HandoverIn,
    HandoverOut,
    HubPickupsOut,
    LocationIn,
    LocationOut,
    PickupsIn,
    PlanKind,
    PlanSummary,
    ProofIn,
    PublishIn,
    ResolveIn,
    RiderRef,
    RouteDetail,
    RoutePatch,
    RoutePlanOut,
    RoutePlansOut,
    RoutePlanSummary,
    RoutesOut,
    SkipIn,
    SocietyRef,
    SolveIn,
    StartIn,
    StopOut,
    StopPatch,
    UndeliveredIn,
    UserRef,
    VehicleIn,
    VehicleKind,
    VehicleOut,
    VehiclePatch,
    VehiclesOut,
)
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import rate_limit
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotency, Idempotent
from gharbasket.platform.pagination import Cursor, decode_cursor, encode_cursor
from gharbasket.platform.permissions import (
    PERMISSIONS,
    Permit,
    active_grants,
    forbidden,
    require,
)
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import Valkey

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
    session: AsyncSession, route: Route, response: Response | None = None, *, staff: bool = False
) -> RouteDetail:
    out = await views.route_detail(session, route, staff=staff)
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
    route, who = await route_ops.get_route(session, route_id, permit)
    return await _detail(session, route, response, staff=who.staff)


@router.patch(
    "/routes/{route_id}",
    summary="Change a route's rider or vehicle before it starts (If-Match: the route's version)",
    description="A new rider gets logistics.route_published again (with previous_rider_user_id).",
    responses={
        **EDITS,
        409: _problem("logistics.routes_started, logistics.route_closed"),
        404: _problem("logistics.route_not_found, logistics.vehicle_not_found"),
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
    out = await _detail(session, route, response, staff=who.staff)
    await session.commit()
    return out


# --- field moves (§3.3) -----------------------------------------------------------------------

RouteDriver = Annotated[Permit, require_any("routes:execute", "routes:write")]
FIELD_MOVES: dict[int | str, dict[str, Any]] = {
    **CREATES,
    403: _problem("auth.forbidden, logistics.not_your_route"),
    409: _problem(
        "logistics.route_not_started, logistics.route_closed, logistics.already_handed_over, "
        "orders.illegal_transition"
    ),
    422: _problem("request.idempotency_mismatch, request.invalid"),
}


async def _stop_answer(
    session: AsyncSession, idem: Idempotency, route: Route, stop: RouteStop, staff: bool
) -> StopOut:
    [out] = await views.stop_views(session, route, [stop], staff=staff)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/routes/{route_id}/start",
    summary="Start a route: its packed orders are dispatched (Idempotency-Key required)",
    description="Today's published route of the caller (or a hub manager for its rider), with no "
    "other route of the rider in progress. Orders still locked or picking refuse the start "
    "(logistics.bags_not_packed) unless leave_behind: they then stay off the route. A tiffin "
    "route (§3.7) needs a rider and moves no order: collect does, at each kitchen.",
    responses={
        **FIELD_MOVES,
        **ROUTE_404,
        409: _problem(
            "logistics.route_not_today, logistics.rider_busy, logistics.bags_not_packed, "
            "logistics.route_not_published, logistics.routes_started, logistics.route_closed, "
            "logistics.rider_required, orders.illegal_transition"
        ),
    },
)
async def start_route(
    route_id: uuid.UUID,
    permit: RouteDriver,
    session: DbSession,
    idem: Idempotent,
    response: Response,
    body: Annotated[StartIn | None, Body()] = None,
) -> RouteDetail:
    body = body or StartIn()
    route, who = await route_ops.get_route(session, route_id, permit, for_update=True, acting=True)
    await execution.start(
        session, route, who, leave_behind=body.leave_behind, client_at=body.client_at
    )
    out = await _detail(session, route, response, staff=who.staff)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/routes/{route_id}/complete",
    summary="Complete a route: bags not handed over ride back to the hub (Idempotency-Key)",
    responses={
        **FIELD_MOVES,
        **ROUTE_404,
        409: _problem(
            "logistics.stops_open, logistics.route_not_started, logistics.route_closed, "
            "orders.illegal_transition"
        ),
    },
)
async def complete_route(
    route_id: uuid.UUID,
    permit: RouteDriver,
    session: DbSession,
    idem: Idempotent,
    response: Response,
    valkey: Valkey,
    body: Annotated[CompleteIn | None, Body()] = None,
) -> RouteDetail:
    body = body or CompleteIn()
    route, who = await route_ops.get_route(session, route_id, permit, for_update=True, acting=True)
    await execution.complete(
        session,
        route,
        who,
        empties_returned=body.empties_returned,
        client_at=body.client_at,
        closer=tiffins.close if route.kind == "tiffin" else None,
    )
    out = await _detail(session, route, response, staff=who.staff)
    await idem.save(200, out)
    await session.commit()
    await tracking.forget(valkey, route.id)  # the closed route's last position is not shown again
    return out


@router.post(
    "/stops/{stop_id}/arrive",
    summary="Record the arrival at a stop and tell the captain (Idempotency-Key)",
    responses={**FIELD_MOVES, 404: _problem("logistics.stop_not_found")},
)
async def arrive_at_stop(
    stop_id: uuid.UUID,
    permit: RouteDriver,
    session: DbSession,
    idem: Idempotent,
    body: Annotated[FieldTap | None, Body()] = None,
) -> StopOut:
    body = body or FieldTap()
    stop, route, who = await execution.get_stop(session, stop_id, permit)
    await execution.arrive(session, route, stop, who, body.client_at)
    return await _stop_answer(session, idem, route, stop, who.staff)


@router.post(
    "/stops/{stop_id}/skip",
    summary="Skip a stop: its bags ride back to the hub (Idempotency-Key)",
    responses={**FIELD_MOVES, 404: _problem("logistics.stop_not_found")},
)
async def skip_stop(
    stop_id: uuid.UUID, body: SkipIn, permit: RouteDriver, session: DbSession, idem: Idempotent
) -> StopOut:
    stop, route, who = await execution.get_stop(session, stop_id, permit)
    await execution.skip(
        session, route, stop, who, reason=body.reason, note=body.note, client_at=body.client_at
    )
    return await _stop_answer(session, idem, route, stop, who.staff)


# --- return-leg pickups (§3.6) ------------------------------------------------------------------

CanReadPickups = Annotated[Permit, require("pickups:read")]


@router.post(
    "/stops/{stop_id}/pickups",
    summary="Report the empties, donations and bags taken back at a stop (Idempotency-Key)",
    description="The route's rider, while it is in progress. A report may name a part: `empties` "
    "replaces the stop's count; each donation and bag is set once. Each change publishes "
    "logistics.pickup_completed (M9 records the donations, M6 unpacks the bags).",
    responses={
        **CREATES,
        404: _problem("logistics.stop_not_found"),
        403: _problem("auth.forbidden, logistics.not_your_route"),
        409: _problem(
            "logistics.route_not_started, logistics.route_closed, logistics.stop_skipped, "
            "logistics.pickup_already_reported (donation_ids, order_ids)"
        ),
        422: _problem(
            "logistics.pickup_unknown (donation_ids, bags), request.invalid, "
            "request.idempotency_mismatch"
        ),
    },
)
async def report_pickups(
    stop_id: uuid.UUID, body: PickupsIn, permit: CanDrive, session: DbSession, idem: Idempotent
) -> StopOut:
    stop, route, who = await execution.get_stop(session, stop_id, permit)
    if route.rider_user_id != who.user_id:  # not a hub manager for him either (§11, decision 5)
        raise ProblemError(403, "logistics.not_your_route", "This route is another rider's.")
    execution.ensure_running(route)
    changed = await pickups.report(session, route, stop, who.user_id, body)
    if changed is not None:
        counts = {
            "empties": changed["empties"],
            "donations": len(changed["donations"]),
            "bags": len(changed["bag_order_ids"]),
        }
        execution.tap(session, route, "pickup", who, body.client_at, stop=stop, payload=counts)
        await session.flush()
    return await _stop_answer(session, idem, route, stop, who.staff)


@router.post(
    "/stops/{stop_id}/collect",
    summary="Collect a kitchen's ready tiffins by its GB1A: code (Idempotency-Key)",
    description="The route's rider at a kitchen stop of his route in progress (§3.7). Each ready "
    "tiffin of the stop goes packed → dispatched as the rider and opens its delivery attempt on "
    "its drop. Scan again later for what was not ready.",
    responses={
        **CREATES,
        404: _problem("logistics.stop_not_found"),
        403: _problem("auth.forbidden, logistics.not_your_route"),
        409: _problem(
            "logistics.route_not_started, logistics.route_closed, logistics.stop_skipped, "
            "logistics.nothing_ready (not_ready), orders.illegal_transition"
        ),
        422: _problem(
            "logistics.not_a_kitchen_stop, logistics.pickup_code_invalid, "
            "logistics.pickup_wrong_kitchen, request.invalid, request.idempotency_mismatch"
        ),
    },
)
async def collect_at_kitchen(
    stop_id: uuid.UUID, body: CollectIn, permit: CanDrive, session: DbSession, idem: Idempotent
) -> CollectOut:
    stop, route, who = await execution.get_stop(session, stop_id, permit)
    collected, already, not_ready, not_on_route = await tiffins.collect(
        session, route, stop, who, body
    )
    [view] = await views.stop_views(session, route, [stop], staff=who.staff)
    out = CollectOut(
        stop=view,
        collected=collected,
        already=already,
        not_ready=not_ready,
        not_on_route=not_on_route,
    )
    await idem.save(200, out)
    await session.commit()
    return out


@router.get(
    "/hubs/{hub_id}/pickups",
    summary="A hub's return-leg pickups for a date, per stop, with totals",
    description="Made when each route starts, so the list is empty before. Flat labels for hub "
    "staff; no names or phones.",
    responses={
        **SIGNED_IN,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("logistics.date_out_of_range"),
    },
)
async def hub_pickups(
    hub_id: uuid.UUID,
    permit: CanReadPickups,
    session: DbSession,
    response: Response,
    day: Day = None,
) -> HubPickupsOut:
    permit.ensure(hub_id)
    await service.ensure_hub(session, hub_id)
    on = _within(day or datetime.now(IST).date(), -7, 1)
    totals, items = await pickups.hub_list(session, hub_id, on)
    response.headers["Cache-Control"] = "no-store"
    return HubPickupsOut(hub_id=hub_id, date=on, totals=totals, items=items)


# --- GPS and live tracking (§3.3) ---------------------------------------------------------------

CanWatch = Annotated[Permit, require("routes:live")]


@router.post(
    "/riders/me/location",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a batch of 1 to 20 GPS pings for the caller's started route",
    description="No Idempotency-Key: pings are deduplicated. A ping too old (10 min), ahead of "
    "the clock (1 min), outside India, less accurate than 200 m or under 5 s after the previous "
    "one is dropped and counted. 12 requests a minute per rider.",
    responses={
        **SIGNED_IN,
        409: _problem("logistics.no_active_route"),
        422: _problem("request.invalid"),
        429: _problem("logistics.location_rate_limited (Retry-After)"),
    },
)
async def post_location(
    body: LocationIn, permit: CanDrive, session: DbSession, valkey: Valkey
) -> LocationOut:
    rider = permit.principal.user_id
    await rate_limit.enforce(
        valkey,
        f"logistics:loc:{rider}",
        limit=12,
        window_s=60,
        code="logistics.location_rate_limited",
        detail="Too many location batches; send at most 12 a minute.",
    )
    route = await tracking.active_route(session, rider, body.route_id)
    accepted = await tracking.accept(session, valkey, route, rider, body.pings)
    await session.commit()
    return LocationOut(accepted=accepted, dropped=len(body.pings) - accepted)


@router.get(
    "/routes/{route_id}/live",
    summary="The van on the map: a Server-Sent Events stream",
    description="Events: `snapshot` first, then `position`, `stop` and `end` (`handed_over`, "
    "`route_completed`, `access_ended`, `reconnect` after 30 minutes); `: keep-alive` every 20 s. "
    "The route's rider and the hub's managers see every stop; a captain sees her stop and a "
    "customer the stop of their order (positions rounded), until its handover.",
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        **SIGNED_IN,
        **ROUTE_404,
        429: _problem("logistics.too_many_streams: more than 3 open streams for this user"),
    },
)
async def live_route(
    route_id: uuid.UUID, permit: CanWatch, session: DbSession, valkey: Valkey
) -> Response:
    route = await session.get(Route, route_id)
    if route is None:
        raise route_ops.route_not_found()
    who = await live.audience(session, permit, route)
    user_id = permit.principal.user_id
    body = live.stream(session, valkey, route.id, who)
    stream_id = await live.claim(valkey, user_id)
    if valkey is not None and stream_id is not None:
        body = live.counted(body, valkey, user_id, stream_id)
    return EventSourceResponse(
        body, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


# --- handover (§3.4) ----------------------------------------------------------------------------

CanHandOver = Annotated[Permit, require("handover:write")]
CanReadDeliveries = Annotated[Permit, require("deliveries:read")]
STOP_404: dict[int | str, dict[str, Any]] = {404: _problem("logistics.stop_not_found")}


@router.patch(
    "/stops/{stop_id}",
    summary="Switch a stop to rider mode for the day (If-Match: the route's version)",
    description="For an absent captain: the route's rider then hands over and does the doors. "
    "Audited.",
    responses={
        **EDITS,
        **STOP_404,
        409: _problem("logistics.route_closed, logistics.already_handed_over"),
    },
)
async def patch_stop(
    stop_id: uuid.UUID,
    body: StopPatch,
    permit: CanSolve,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> StopOut:
    stop, route, who = await execution.get_stop(session, stop_id, permit)
    if not who.manager:
        raise execution.stop_not_found()
    await handover.set_rider_mode(session, route, stop, who, body.rider_mode, expected)
    [out] = await views.stop_views(session, route, [stop], staff=True)
    await session.commit()
    set_etag(response, route.version)
    return out


def _captain_society(permit: Permit) -> uuid.UUID:
    societies = {g.scope_id for g in permit.grants if g.role == "captain" and g.scope_id}
    if len(societies) != 1:
        raise forbidden("Choose your society with X-Active-Role: captain:<society>.")
    return societies.pop()


@router.get(
    "/captains/me/today",
    summary="The captain's day: the van's stops, the flats of her society and their counts",
    description="The society of X-Active-Role (captain:<society>), today or yesterday. Flat "
    "labels only: no names, no phones.",
    responses={
        **SIGNED_IN,
        403: _problem("auth.forbidden, logistics.not_society_captain"),
        422: _problem("logistics.date_out_of_range"),
    },
)
async def captain_today(
    permit: CanReadDeliveries, session: DbSession, response: Response, day: Day = None
) -> CaptainDay:
    society_id = _captain_society(permit)
    on = _within(day or datetime.now(IST).date(), -1, 0)
    await handover.ensure_captain(session, permit.principal.user_id, society_id)
    society = await community_api.get_society(session, society_id)
    labels = await community_api.society_labels(session, [society_id])
    stops = []
    for stop, route in await handover.society_stops(session, society_id, on):
        if route.kind == "tiffin" and stop.rider_mode:  # the rider's doors, not hers (§3.7)
            continue
        [view] = await views.stop_views(session, route, [stop])
        all_etas = views.eta_now(route, await views.stops_of(session, route.id))
        stops.append(
            CaptainStop(
                stop_id=stop.id,
                route_id=route.id,
                slot=view.slot,
                eta_now=all_etas[stop.id],
                status=view.status,
                expected=Expected(
                    crates=stop.crates, bags=stop.bags, redelivery_bags=view.redelivery_bags
                ),
            )
        )
    hub_id = society.hub_id if society else None
    mine = await handover.tiffins_of(session, society_id=society_id, day=on)
    flats = (
        []
        if hub_id is None
        else await handover.flats(session, hub_id, on, society_id, tiffins=mine)
    )
    counts = FlatCounts()
    for flat in flats:
        setattr(counts, flat.status, getattr(counts, flat.status) + 1)
    response.headers["Cache-Control"] = "no-store"
    label = labels.get(society_id)
    return CaptainDay(
        date=on,
        society=SocietyRef(id=society_id, name=label.name if label else None),
        stops=stops,
        flats=flats,
        counts=counts,
    )


@router.get(
    "/stops/{stop_id}/deliveries",
    summary="A stop's flats: for the rider of a rider-mode stop while the route runs, hub staff",
    responses={**SIGNED_IN, **STOP_404},
)
async def stop_deliveries(
    stop_id: uuid.UUID, permit: CanReadDeliveries, session: DbSession, response: Response
) -> FlatsOut:
    stop = await session.get(RouteStop, stop_id)
    route = None if stop is None else await session.get(Route, stop.route_id)
    if stop is None or route is None or stop.society_id is None:
        raise execution.stop_not_found()
    who = route_ops.caller(permit, route.hub_id)
    riding = (
        who.rider
        and route.rider_user_id == who.user_id
        and stop.rider_mode
        and route.status == "in_progress"
    )
    if not (who.staff or riding):
        raise execution.stop_not_found()
    if route.kind == "tiffin":  # the drop's tiffins, with their short codes (§3.7)
        items = await handover.flats(
            session,
            route.hub_id,
            route.delivery_date,
            stop.society_id,
            tiffins=await handover.tiffins_of(session, stop_id=stop.id),
            boxes=False,
        )
    else:
        items = await handover.flats(
            session, route.hub_id, route.delivery_date, stop.society_id, stop.slot_id
        )
    response.headers["Cache-Control"] = "no-store"
    return FlatsOut(items=items)


@router.post(
    "/stops/{stop_id}/handover",
    summary="Hand a stop's crates to its captain by count and code or photo (Idempotency-Key)",
    description="Matching counts move every dispatched order of the stop to at_society; otherwise "
    "only the scanned bags move and the rest ride back. Rider mode needs no code or photo. Five "
    "wrong codes for a stop allow only a photo. A tiffin drop (§3.7) is counted with crates 0 and "
    "no scans: its tiffins become with_captain without an order move (count_mismatch names the "
    "expected short_codes).",
    responses={
        **CREATES,
        **STOP_404,
        403: _problem("auth.forbidden, logistics.not_society_captain, logistics.not_your_route"),
        409: _problem(
            "logistics.count_mismatch (expected, short_codes), logistics.already_handed_over, "
            "logistics.route_not_started, logistics.route_closed, orders.illegal_transition"
        ),
        422: _problem(
            "logistics.code_invalid, logistics.photo_key_invalid, request.invalid, "
            "request.idempotency_mismatch"
        ),
        429: _problem("logistics.code_attempts_exceeded"),
    },
)
async def hand_over(
    stop_id: uuid.UUID,
    body: HandoverIn,
    permit: CanHandOver,
    session: DbSession,
    idem: Idempotent,
    valkey: Valkey,
    request: Request,
) -> HandoverOut:
    stop, route, actor = await handover.stop_actor(session, permit, stop_id)
    store = getattr(request.app.state, "storage", None)
    photo_valid = body.photo_key is not None and await handover.photo_ok(
        session, store, body.photo_key, actor.user_id
    )
    try:
        proof = handover.check_proof(route, stop, actor, body, photo_valid)
    except handover.CodeInvalid as wrong:
        # The wrong try counts, and a replay of this key answers the same.
        await idem.save(422, {"status": 422, "code": wrong.code, "detail": wrong.detail})
        await session.commit()
        raise
    taken: list[uuid.UUID] = []
    if route.kind == "tiffin":  # by count, no M4 move (§3.7)
        made, taken = await tiffins.hand_over(session, valkey, route, stop, actor, body, proof)
        moved, still = [], []
    else:
        made, moved, still = await handover.hand_over(
            session, valkey, route, stop, actor, body, proof
        )
    [view] = await views.stop_views(session, route, [stop], staff=actor.kind == "hub")
    if actor.kind == "captain":  # the code is the rider's and hub staff's only
        view.handover_code = None
    out = HandoverOut(
        handover_id=made.id,
        stop=view,
        at_society=moved,
        still_dispatched=still,
        with_captain=taken,
        mismatch=made.mismatch,
    )
    await idem.save(200, out)
    await session.commit()
    return out


# --- doors (§3.4) and the customer's card (§3.5) ------------------------------------------------

CanDeliver = Annotated[Permit, require("deliveries:write")]
CanResolve = Annotated[Permit, require("deliveries:resolve")]
DELIVERY_404: dict[int | str, dict[str, Any]] = {404: _problem("logistics.delivery_not_found")}
DOOR_MOVES: dict[int | str, dict[str, Any]] = {
    **CREATES,
    **DELIVERY_404,
    403: _problem("auth.forbidden, logistics.not_society_captain"),
    409: _problem("logistics.delivery_closed, orders.illegal_transition"),
}


async def _door_answer(
    session: AsyncSession, idem: Idempotency, order_id: uuid.UUID
) -> DeliveryOut:
    attempt = await attempts.latest(session, order_id)
    assert attempt is not None
    out = attempts.delivery_out(attempt)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/deliveries/{order_id}/proof",
    summary="Deliver a bag at the door with its scan, the household's code or a photo",
    description="Exactly one of the bag QR and a tiffin's short code, which must be this "
    "order's. A captain may scan a box alone; a rider, and anyone delivering a tiffin (§3.7), "
    "needs otp or photo. Five wrong codes for an order allow only a photo. Idempotency-Key "
    "required.",
    responses={
        **DOOR_MOVES,
        422: _problem(
            "logistics.bag_mismatch, logistics.proof_required, logistics.code_invalid, "
            "logistics.photo_key_invalid, request.invalid"
        ),
        429: _problem("logistics.code_attempts_exceeded"),
    },
)
async def prove_delivery(
    order_id: uuid.UUID,
    body: ProofIn,
    permit: CanDeliver,
    session: DbSession,
    idem: Idempotent,
    request: Request,
) -> DeliveryOut:
    attempt, order, actor = await deliveries.door_actor(session, permit, order_id)
    store = getattr(request.app.state, "storage", None)
    photo_valid = (
        body.proof == "photo"  # a photo sent with a scan or a code is not stored, so not checked
        and body.photo_key is not None
        and await handover.photo_ok(session, store, body.photo_key, actor.user_id)
    )
    try:
        deliveries.check_proof(attempt, order, actor, body, photo_valid)
    except handover.CodeInvalid as wrong:
        await idem.save(422, {"status": 422, "code": wrong.code, "detail": wrong.detail})
        await session.commit()
        raise
    await deliveries.deliver(session, attempt, order, actor, body)
    return await _door_answer(session, idem, order_id)


@router.post(
    "/deliveries/{order_id}/undelivered",
    summary="Nobody home, refused, damaged…: hold, send back, refund or triage (Idempotency-Key)",
    description="A captain holds a not-home bag until 20:00; a rider's not-home bag rides back. "
    "Damaged or missing is refunded in full at once; refused, wrong address and other go to the "
    "hub's triage. A tiffin (§3.7) is never held: any reason refunds it in full at once.",
    responses={**DOOR_MOVES, 422: _problem("logistics.photo_key_invalid, request.invalid")},
)
async def mark_undelivered(
    order_id: uuid.UUID,
    body: UndeliveredIn,
    permit: CanDeliver,
    session: DbSession,
    idem: Idempotent,
    request: Request,
) -> DeliveryOut:
    attempt, order, actor = await deliveries.door_actor(session, permit, order_id)
    store = getattr(request.app.state, "storage", None)
    if body.photo_key is not None and not await handover.photo_ok(
        session, store, body.photo_key, actor.user_id
    ):
        raise ProblemError(
            422, "logistics.photo_key_invalid", "Upload the photo as delivery_photo first."
        )
    await deliveries.fail(session, attempt, order, actor, body)
    return await _door_answer(session, idem, order_id)


@router.post(
    "/deliveries/{order_id}/resolve",
    summary="Triage an undelivered order: the free redelivery or a refund (Idempotency-Key)",
    description="`redeliver` books the one free redelivery (attempt 1 only); a bag the day's route "
    "left behind rides again as attempt 1; a redelivery no route picked up is booked again. "
    "`refund` pays the wallet all that is left of the order's paid total; a smaller "
    "`refund_paise` ends the order with the rest unrefunded, so it needs `final: true` (else 422 "
    "`logistics.partial_refund_not_final`). When the wallet cannot take the full amount (422 "
    "`billing.wallet_limit` with `room_paise`) nothing has changed and the attempt stays in "
    "triage until the customer has room, or until M5 offers a per-order refund to the "
    "payment source.",
    responses={
        **CREATES,
        **DELIVERY_404,
        409: _problem(
            "logistics.not_undelivered, logistics.no_redelivery, orders.illegal_transition"
        ),
        422: _problem(
            "logistics.refund_invalid (max_paise), logistics.partial_refund_not_final "
            "(max_paise), billing.wallet_limit (room_paise), request.invalid"
        ),
    },
)
async def resolve_delivery(
    order_id: uuid.UUID, body: ResolveIn, permit: CanResolve, session: DbSession, idem: Idempotent
) -> DeliveryOut:
    if await attempts.latest(session, order_id) is None:
        raise deliveries.delivery_not_found()
    order = await orders_api.get(session, order_id, for_update=True)  # the order, then its attempt
    attempt = await attempts.latest(session, order_id, for_update=True)
    if attempt is None or not permit.allows(order.hub_id):
        raise deliveries.delivery_not_found()
    tomorrow = datetime.now(IST).date() + timedelta(days=1)
    await deliveries.resolve(session, attempt, order, permit.principal.user_id, body, tomorrow)
    return await _door_answer(session, idem, order_id)


@router.get(
    "/deliveries/{order_id}",
    summary="The order's delivery card: window, ETA, tracking, captain, hold, code and proof",
    description="For the order's household (any member) and hub staff. The delivery code goes "
    "only to the household while the order is dispatched or at the society.",
    responses={**SIGNED_IN, **DELIVERY_404},
)
async def get_delivery(
    order_id: uuid.UUID,
    permit: CanReadDeliveries,
    session: DbSession,
    request: Request,
    response: Response,
) -> DeliveryCard:
    try:
        order = await orders_api.get(session, order_id)
    except ProblemError:
        raise deliveries.delivery_not_found() from None
    staff = route_ops.caller(permit, order.hub_id).staff
    household = False
    if any(g.role == "customer" for g in permit.grants):
        mine = await identity_api.household_for_user(session, permit.principal.user_id)
        household = mine is not None and mine.id == order.household_id
    if not (staff or household):
        raise deliveries.delivery_not_found()
    out = await card.delivery_card(
        session,
        order,
        household=household,
        store=getattr(request.app.state, "storage", None),
    )
    response.headers["Cache-Control"] = "no-store"
    return out


@router.get(
    "/hubs/{hub_id}/deliveries",
    summary="The hub's deliveries for a date (the exceptions board), oldest first, with a cursor",
    responses={**SIGNED_IN, 422: _problem("logistics.date_out_of_range, request.invalid_cursor")},
)
async def hub_deliveries(
    hub_id: uuid.UUID,
    permit: CanReadDeliveries,
    session: DbSession,
    response: Response,
    day: Day = None,
    delivery_status: Annotated[DeliveryStatus | None, Query(alias="status")] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DeliveriesPage:
    roles = {g.role for g in permit.grants if g.scope_id in (None, hub_id)}
    if not roles & {"hub_manager", "franchise_owner", "admin"}:
        raise forbidden()
    after = None
    if cursor is not None:
        try:
            after = uuid.UUID(str(decode_cursor(cursor).get("after")))
        except ValueError:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from None
    on = _within(day or datetime.now(IST).date(), -7, 1)
    rows = await card.board(session, hub_id, on, delivery_status, after, limit + 1)
    page = rows[:limit]
    response.headers["Cache-Control"] = "no-store"
    first = after is None
    return DeliveriesPage(
        items=[attempts.delivery_out(r) for r in page],
        next_cursor=encode_cursor({"after": str(page[-1].id)}) if len(rows) > limit else None,
        left_behind=await card.left_behind(session, hub_id, on) if first else [],
        orphans=await card.orphans(session, hub_id, on) if first else [],
    )
