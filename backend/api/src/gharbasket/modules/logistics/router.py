"""HTTP endpoints of M7 Logistics: vehicles (spec §3.1)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.logistics import service
from gharbasket.modules.logistics.models import Vehicle
from gharbasket.modules.logistics.schemas import (
    RiderRef,
    VehicleIn,
    VehicleKind,
    VehicleOut,
    VehiclePatch,
    VehiclesOut,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require

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
