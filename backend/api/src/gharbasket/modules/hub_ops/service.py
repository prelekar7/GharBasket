"""Business logic of M6 Hub Operations: hubs, service areas and vendors (spec §3.1).

Vendor writes lock the hub row first, so two requests making different vendors the default (or
adding the same name) at one hub run one after the other.
"""

import json
import re
import uuid
from itertools import pairwise
from typing import Any

from geoalchemy2 import WKTElement
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import events
from gharbasket.modules.hub_ops.models import DEFAULT_TIMINGS, TIMING_KEYS, Hub, Vendor
from gharbasket.modules.hub_ops.schemas import (
    GeoPolygon,
    HubIn,
    HubPatch,
    TimingsPatch,
    VendorIn,
    VendorPatch,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import LAT_RANGE, LNG_RANGE, point
from gharbasket.platform.outbox import publish

HHMM = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
RING_POINTS = (4, 500)


def hub_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.hub_not_found", "No such hub.")


def vendor_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.vendor_not_found", "No such vendor.")


def check_version(current: int, expected: int) -> None:
    if current != expected:
        raise ProblemError(
            412, "request.version_mismatch", "This changed since you loaded it; reload and retry."
        )


# --- service areas and timings -------------------------------------------------------------------


def _area_invalid(detail: str) -> ProblemError:
    return ProblemError(422, "hub_ops.area_invalid", detail)


async def area(session: AsyncSession, polygon: GeoPolygon) -> WKTElement:
    """A GeoJSON polygon as a value for the `service_area` column, or 422 `hub_ops.area_invalid`."""
    if len(polygon.coordinates) != 1:
        raise _area_invalid("The area is one ring, without holes.")
    ring = polygon.coordinates[0]
    if not RING_POINTS[0] <= len(ring) <= RING_POINTS[1]:
        raise _area_invalid("The ring has 4 to 500 points.")
    for corner in ring:
        # NaN fails every comparison, so it is refused here too.
        if len(corner) != 2 or not (
            LNG_RANGE[0] <= corner[0] <= LNG_RANGE[1] and LAT_RANGE[0] <= corner[1] <= LAT_RANGE[1]
        ):
            raise _area_invalid("Each point is [lng, lat] in India.")
    if ring[0] != ring[-1]:
        raise _area_invalid("The ring is closed: its last point repeats the first.")
    wkt = f"POLYGON(({', '.join(f'{lng!r} {lat!r}' for lng, lat in ring)}))"
    valid = await session.scalar(select(func.ST_IsValid(func.ST_GeomFromText(wkt, 4326))))
    if not valid:
        raise _area_invalid("The area crosses itself or has no inside.")
    return WKTElement(wkt, srid=4326, extended=False)


def _area_coordinates(hub: Hub) -> list[list[list[float]]] | None:
    stored = hub.service_area_geojson
    return None if stored is None else json.loads(stored)["coordinates"]


def timings(current: dict[str, Any], patch: TimingsPatch | None) -> dict[str, str]:
    """`current` with the times sent, or 422 `hub_ops.timings_invalid`."""
    merged = {**current, **(patch.model_dump(exclude_none=True) if patch else {})}
    clock = [merged[key] for key in TIMING_KEYS]
    if not all(HHMM.fullmatch(time) for time in clock) or any(
        later <= earlier for earlier, later in pairwise(clock)
    ):
        raise ProblemError(
            422,
            "hub_ops.timings_invalid",
            "Times are HH:MM, in the order receiving < packing < dispatch < surplus.",
        )
    return dict(zip(TIMING_KEYS, clock, strict=True))


# --- hubs ---------------------------------------------------------------------------------------


async def list_hubs(session: AsyncSession, ids: set[uuid.UUID] | None) -> list[Hub]:
    """Every hub (`ids` None) or those hubs, by name."""
    stmt = select(Hub).order_by(Hub.name, Hub.id)
    if ids is not None:
        stmt = stmt.where(Hub.id.in_(ids))
    return list((await session.execute(stmt)).scalars())


async def get_hub(session: AsyncSession, hub_id: uuid.UUID, *, for_update: bool = False) -> Hub:
    """The hub; `for_update` row-locks it so the hub's writers follow one another. The lock is
    `FOR NO KEY UPDATE`: it never blocks a row that only refers to the hub (a foreign-key check
    takes `KEY SHARE`), so a settle or a write-off that holds its own rows and inserts a waste log
    cannot deadlock with the 12:30 run holding the hub and waiting for those rows."""
    stmt = select(Hub).where(Hub.id == hub_id)
    if for_update:
        stmt = stmt.with_for_update(key_share=True)
    hub = (await session.execute(stmt)).scalar_one_or_none()
    if hub is None:
        raise hub_not_found()
    return hub


async def create_hub(
    session: AsyncSession,
    body: HubIn,
    *,
    hub_id: uuid.UUID | None = None,
    data_source: str = "real",
) -> Hub:
    hub = Hub(
        slug=body.slug,
        name=body.name,
        address=body.address,
        location=point(body.location.lat, body.location.lng),
        service_area=None if body.service_area is None else await area(session, body.service_area),
        timings=timings(DEFAULT_TIMINGS, body.timings),
        procurement_buffer_pct=body.procurement_buffer_pct,
        pre_live_box_fee_paise=body.pre_live_box_fee_paise,
        green_saving_share_pct=body.green_saving_share_pct,
        green_saving_max_paise=body.green_saving_max_paise,
        data_source=data_source,
    )
    if hub_id is not None:
        hub.id = hub_id
    try:
        async with session.begin_nested():
            session.add(hub)
            await session.flush()
    except IntegrityError:
        raise ProblemError(409, "hub_ops.slug_taken", "A hub with this slug exists.") from None
    await session.refresh(hub)
    await publish(
        session, events.HUB_CREATED, {"hub_id": str(hub.id), "slug": hub.slug, "name": hub.name}
    )
    return hub


async def update_hub(session: AsyncSession, hub: Hub, body: HubPatch) -> list[str]:
    """Apply the fields sent; returns the names of those that changed (version + 1 if any)."""
    sent = body.model_fields_set
    changed: list[str] = []
    for name in (
        "name",
        "address",
        "status",
        "procurement_buffer_pct",
        "pre_live_box_fee_paise",
        "green_saving_share_pct",
        "green_saving_max_paise",
    ):
        if name in sent and getattr(body, name) != getattr(hub, name):
            setattr(hub, name, getattr(body, name))
            changed.append(name)
    if body.location is not None and (body.location.lat, body.location.lng) != (hub.lat, hub.lng):
        hub.location = point(body.location.lat, body.location.lng)
        changed.append("location")
    new_area = None if body.service_area is None else body.service_area.coordinates
    if "service_area" in sent and new_area != _area_coordinates(hub):
        hub.service_area = (
            None if body.service_area is None else await area(session, body.service_area)
        )
        changed.append("service_area")
    if body.timings is not None:
        new_timings = timings(hub.timings, body.timings)
        if new_timings != hub.timings:
            hub.timings = new_timings
            changed.append("timings")
    if changed:
        hub.version += 1
        await session.flush()
        await session.refresh(hub)
        await publish(session, events.HUB_UPDATED, {"hub_id": str(hub.id), "changed": changed})
    return changed


async def active_hub_ids(session: AsyncSession) -> list[uuid.UUID]:
    rows = await session.execute(select(Hub.id).where(Hub.status == "active").order_by(Hub.id))
    return list(rows.scalars())


# --- vendors ------------------------------------------------------------------------------------


async def list_vendors(
    session: AsyncSession, hub_id: uuid.UUID, active: bool | None
) -> list[Vendor]:
    stmt = select(Vendor).where(Vendor.hub_id == hub_id).order_by(func.lower(Vendor.name))
    if active is not None:
        stmt = stmt.where(Vendor.active.is_(active))
    return list((await session.execute(stmt)).scalars())


async def get_vendor(session: AsyncSession, vendor_id: uuid.UUID) -> Vendor:
    vendor = await session.get(Vendor, vendor_id)
    if vendor is None:
        raise vendor_not_found()
    return vendor


async def _clear_default(session: AsyncSession, hub_id: uuid.UUID) -> None:
    """The hub's current default vendor stops being the default (version + 1)."""
    await session.execute(
        update(Vendor)
        .where(Vendor.hub_id == hub_id, Vendor.is_default)
        .values(is_default=False, version=Vendor.version + 1)
        .execution_options(synchronize_session="fetch")
    )


async def _save_vendor(session: AsyncSession, vendor: Vendor) -> Vendor:
    try:
        async with session.begin_nested():
            session.add(vendor)
            await session.flush()
    except IntegrityError:
        raise ProblemError(
            409, "hub_ops.vendor_name_taken", "This hub has a vendor with that name."
        ) from None
    await session.refresh(vendor)
    return vendor


async def create_vendor(session: AsyncSession, hub: Hub, body: VendorIn) -> Vendor:
    """`hub` is locked by the caller (`get_hub(..., for_update=True)`)."""
    if body.is_default:
        await _clear_default(session, hub.id)
    vendor = Vendor(
        hub_id=hub.id,
        name=body.name,
        kind=body.kind,
        phone_e164=body.phone,
        whatsapp=body.whatsapp,
        is_default=body.is_default,
        notes=body.notes,
    )
    return await _save_vendor(session, vendor)


async def update_vendor(
    session: AsyncSession, vendor: Vendor, body: VendorPatch, expected_version: int
) -> Vendor:
    await get_hub(session, vendor.hub_id, for_update=True)
    await session.refresh(vendor)  # what it is now that the hub is ours
    check_version(vendor.version, expected_version)
    values = {
        ("phone_e164" if name == "phone" else name): getattr(body, name)
        for name in body.model_fields_set
    }
    active = values.get("active", vendor.active)
    # An inactive vendor is never the default: deactivating clears it.
    values["is_default"] = active and values.get("is_default", vendor.is_default)
    if values["is_default"] and not vendor.is_default:
        await _clear_default(session, vendor.hub_id)
    changed = {name: value for name, value in values.items() if getattr(vendor, name) != value}
    if not changed:
        return vendor
    for name, value in changed.items():
        setattr(vendor, name, value)
    vendor.version += 1
    return await _save_vendor(session, vendor)
