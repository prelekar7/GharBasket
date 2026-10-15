"""Routes (spec §3.3): who may read a route, the hub's and the rider's lists, and the hub
manager's edits before the start (rider and vehicle).

Hub staff of a route's hub read it; its rider reads it once it is published. Anyone else gets 404
`logistics.route_not_found`, as if it did not exist. Every change to a route or one of its stops
bumps the route's version (its ETag).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import plans
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.modules.logistics.schemas import RoutePatch
from gharbasket.modules.logistics.service import CARRIES, check_version, vehicle_not_found
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit

STAFF = ("hub_manager", "franchise_owner", "packer", "admin")
VEHICLE_FOR = {"box": "cargo_3w", "tiffin": "two_wheeler"}


def route_not_found() -> ProblemError:
    return ProblemError(404, "logistics.route_not_found", "No such route.")


def conflict(code: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(409, code, detail, extra=extra or None)


@dataclass(frozen=True)
class Caller:
    user_id: uuid.UUID
    manager: bool  # a hub manager of the route's hub
    staff: bool  # hub staff who may read the hub's routes
    rider: bool  # holds rider:<the route's hub>


def caller(permit: Permit, hub_id: uuid.UUID) -> Caller:
    roles = {g.role for g in permit.grants if g.scope_id in (None, hub_id)}
    return Caller(
        permit.principal.user_id,
        manager="hub_manager" in roles,
        staff=bool(roles & set(STAFF)),
        rider="rider" in roles,
    )


def _visible(route: Route, who: Caller) -> bool:
    own = who.rider and route.rider_user_id == who.user_id and route.status != "draft"
    return who.staff or own


async def get_route(
    session: AsyncSession, route_id: uuid.UUID, permit: Permit, *, for_update: bool = False
) -> tuple[Route, Caller]:
    """The route, or 404 `logistics.route_not_found` (also outside the caller's scope)."""
    route = await session.get(Route, route_id, with_for_update=for_update)
    if route is None:
        raise route_not_found()
    who = caller(permit, route.hub_id)
    if not _visible(route, who):
        raise route_not_found()
    return route, who


async def edit(
    session: AsyncSession, route: Route, who: Caller, body: RoutePatch, expected: int
) -> Route:
    """Change the rider or the vehicle of a route that has not started (§3.3)."""
    if route.status in ("completed", "cancelled"):
        raise conflict("logistics.route_closed", f"This route is {route.status}.")
    if route.status == "in_progress":
        raise conflict("logistics.routes_started", "This route has already started.")
    check_version(route.version, expected)
    before = {"rider_user_id": route.rider_user_id, "vehicle_id": route.vehicle_id}
    changed = {
        name: getattr(body, name)
        for name in body.model_fields_set
        if getattr(body, name) != getattr(route, name)
    }
    if not changed:
        return route
    rider = changed.get("rider_user_id")
    if rider is not None and not await identity_api.holds_role(
        session, rider, "rider", route.hub_id
    ):
        raise ProblemError(
            422,
            "logistics.rider_invalid",
            "The rider must be a rider of this hub.",
            extra={"field": "rider_user_id"},
        )
    if "vehicle_id" in changed:
        await _check_vehicle(session, route, changed["vehicle_id"])
    for name, value in changed.items():
        setattr(route, name, value)
    route.version += 1
    await session.flush()
    await audit.record(
        session,
        actor_user_id=who.user_id,
        action="routes.edited",
        entity_type="route",
        entity_id=str(route.id),
        before={k: None if v is None else str(v) for k, v in before.items() if k in changed},
        after={k: None if v is None else str(v) for k, v in changed.items()},
    )
    if "rider_user_id" in changed and route.status == "published":
        plan = await session.get(RoutePlan, route.plan_id)
        assert plan is not None
        await plans.route_published(session, plan, route, before["rider_user_id"])
    return route


async def _check_vehicle(session: AsyncSession, route: Route, vehicle_id: uuid.UUID) -> None:
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.hub_id != route.hub_id:
        raise vehicle_not_found()
    if not vehicle.active or vehicle.kind != VEHICLE_FOR[route.kind]:
        raise ProblemError(
            422,
            "request.invalid",
            f"Choose an active {VEHICLE_FOR[route.kind]} of this hub.",
            extra={"field": "vehicle_id"},
        )
    taken = await session.scalar(
        select(Route.id).where(
            Route.plan_id == route.plan_id,
            Route.vehicle_id == vehicle_id,
            Route.trip == route.trip,
            Route.id != route.id,
        )
    )
    if taken is not None:
        raise ProblemError(
            422,
            "request.invalid",
            f"That vehicle already drives trip {route.trip} of this plan.",
            extra={"field": "vehicle_id"},
        )
    load = (
        await session.execute(
            select(
                func.coalesce(func.sum(RouteStop.kg_tenths), 0),
                func.coalesce(func.sum(RouteStop.crates), 0),
            ).where(RouteStop.route_id == route.id)
        )
    ).one()
    carries = CARRIES[vehicle.kind]
    if load[0] > vehicle.capacity_kg * 10 or (
        carries == "capacity_crates" and load[1] > vehicle.capacity_crates
    ):
        raise ProblemError(
            422,
            "logistics.vehicle_too_small",
            f"{vehicle.label} cannot carry this route's load.",
            extra={"kg": load[0] / 10, "crates": int(load[1])},
        )


# --- reads ----------------------------------------------------------------------------------------


async def hub_routes(
    session: AsyncSession, hub_id: uuid.UUID, day: date, kind: str | None, status: str | None
) -> list[plans.RouteRow]:
    where = [Route.hub_id == hub_id, Route.delivery_date == day]
    if kind is not None:
        where.append(Route.kind == kind)
    if status is not None:
        where.append(Route.status == status)
    return await plans.route_rows(session, *where)


async def rider_routes(session: AsyncSession, permit: Permit, day: date) -> list[plans.RouteRow]:
    hubs = [g.scope_id for g in permit.grants if g.role == "rider" and g.scope_id is not None]
    return await plans.route_rows(
        session,
        Route.rider_user_id == permit.principal.user_id,
        Route.hub_id.in_(hubs),
        Route.delivery_date == day,
        Route.status.in_(("published", "in_progress", "completed")),
    )
