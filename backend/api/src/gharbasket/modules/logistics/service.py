"""Business logic of M7 Logistics: vehicles (spec §3.1).

Vehicle writes take a transaction-scoped advisory lock on the hub, so two requests adding the same
label at one hub run one after the other; the unique index on (hub_id, lower(label)) is the last
word either way.
"""

import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics.models import Vehicle
from gharbasket.modules.logistics.schemas import VehicleIn, VehiclePatch
from gharbasket.platform.errors import ProblemError

# Capacities a vehicle gets when the request leaves them out (spec §3.1; ADR-0026 §6).
DEFAULT_CAPACITY: dict[str, dict[str, int]] = {
    "cargo_3w": {"capacity_kg": 400, "capacity_crates": 24, "capacity_tiffins": 0},
    "two_wheeler": {"capacity_kg": 40, "capacity_crates": 0, "capacity_tiffins": 30},
}
# What each kind must be able to carry: boxes and add-ons ride in crates on a cargo_3w,
# tiffins on a two_wheeler.
CARRIES = {"cargo_3w": "capacity_crates", "two_wheeler": "capacity_tiffins"}


def vehicle_not_found() -> ProblemError:
    return ProblemError(404, "logistics.vehicle_not_found", "No such vehicle.")


def hub_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.hub_not_found", "No such hub.")


def check_version(current: int, expected: int) -> None:
    if current != expected:
        raise ProblemError(
            412, "request.version_mismatch", "This changed since you loaded it; reload and retry."
        )


async def ensure_hub(session: AsyncSession, hub_id: uuid.UUID) -> hub_ops_api.HubRef:
    hub = await hub_ops_api.get_hub(session, hub_id)
    if hub is None:
        raise hub_not_found()
    return hub


async def _lock_hub(session: AsyncSession, hub_id: uuid.UUID) -> None:
    """Serialise vehicle writes at one hub (M6 owns the hub row, so M7 locks a key instead)."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"logistics:vehicles:{hub_id}"},
    )


async def list_vehicles(
    session: AsyncSession, hub_id: uuid.UUID, active: bool | None, kind: str | None
) -> list[Vehicle]:
    stmt = select(Vehicle).where(Vehicle.hub_id == hub_id)
    if active is not None:
        stmt = stmt.where(Vehicle.active.is_(active))
    if kind is not None:
        stmt = stmt.where(Vehicle.kind == kind)
    stmt = stmt.order_by(func.lower(Vehicle.label), Vehicle.id)
    return list((await session.execute(stmt)).scalars())


async def get_vehicle(session: AsyncSession, vehicle_id: uuid.UUID) -> Vehicle:
    vehicle = await session.get(Vehicle, vehicle_id)
    if vehicle is None:
        raise vehicle_not_found()
    return vehicle


async def rider_names(session: AsyncSession, vehicles: list[Vehicle]) -> dict[uuid.UUID, str]:
    ids = {v.default_rider_user_id for v in vehicles if v.default_rider_user_id is not None}
    return await identity_api.user_names(session, sorted(ids))


async def _check_rider(session: AsyncSession, hub_id: uuid.UUID, user_id: uuid.UUID) -> None:
    if not await identity_api.holds_role(session, user_id, "rider", hub_id):
        raise ProblemError(
            422,
            "logistics.rider_invalid",
            "The default rider must be a rider of this hub.",
            extra={"field": "default_rider_user_id"},
        )


def _check_capacity(kind: str, capacities: dict[str, int]) -> None:
    needed = CARRIES[kind]
    if capacities[needed] < 1:
        raise ProblemError(
            422,
            "logistics.capacity_invalid",
            f"A {kind} vehicle needs {needed} of at least 1.",
            extra={"field": needed},
        )


async def _save(session: AsyncSession, vehicle: Vehicle) -> Vehicle:
    try:
        async with session.begin_nested():
            session.add(vehicle)
            await session.flush()
    except IntegrityError:
        raise ProblemError(
            409, "logistics.vehicle_label_taken", "This hub has a vehicle with that label."
        ) from None
    await session.refresh(vehicle)
    return vehicle


async def create_vehicle(session: AsyncSession, hub_id: uuid.UUID, body: VehicleIn) -> Vehicle:
    await ensure_hub(session, hub_id)
    await _lock_hub(session, hub_id)
    capacities = {
        name: default if getattr(body, name) is None else getattr(body, name)
        for name, default in DEFAULT_CAPACITY[body.kind].items()
    }
    _check_capacity(body.kind, capacities)
    if body.default_rider_user_id is not None:
        await _check_rider(session, hub_id, body.default_rider_user_id)
    vehicle = Vehicle(
        hub_id=hub_id,
        label=body.label,
        kind=body.kind,
        registration=body.registration,
        default_rider_user_id=body.default_rider_user_id,
        **capacities,
    )
    return await _save(session, vehicle)


async def update_vehicle(
    session: AsyncSession, vehicle: Vehicle, body: VehiclePatch, expected_version: int
) -> Vehicle:
    """Only the fields sent change; version + 1 when anything did."""
    await _lock_hub(session, vehicle.hub_id)
    await session.refresh(vehicle)  # what it is now that the hub is ours
    check_version(vehicle.version, expected_version)
    values = {name: getattr(body, name) for name in body.model_fields_set}
    changed = {name: v for name, v in values.items() if getattr(vehicle, name) != v}
    if not changed:
        return vehicle
    kind = changed.get("kind", vehicle.kind)
    capacities = {name: changed.get(name, getattr(vehicle, name)) for name in CARRIES.values()}
    _check_capacity(kind, capacities)
    rider = changed.get("default_rider_user_id")
    if rider is not None:
        await _check_rider(session, vehicle.hub_id, rider)
    for name, value in changed.items():
        setattr(vehicle, name, value)
    vehicle.version += 1
    return await _save(session, vehicle)
