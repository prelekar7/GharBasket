"""HTTP endpoints of M6 Hub Operations: hubs, service areas and vendors (spec §3.1) and packing
(§3.2)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Body, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import jobs as _jobs  # noqa: F401 (registers the jobs)
from gharbasket.modules.hub_ops import packing, service
from gharbasket.modules.hub_ops import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.hub_ops.models import Hub, PackingTask, Vendor
from gharbasket.modules.hub_ops.schemas import (
    BuildIn,
    BuildOut,
    CompleteIn,
    GeoPolygon,
    HubIn,
    HubOut,
    HubPatch,
    HubsOut,
    HubSummary,
    PackingSummary,
    PackingTaskDetail,
    PackingTaskOut,
    PickIn,
    ShortageIn,
    StartIn,
    TaskStatus,
    Timings,
    VendorIn,
    VendorOut,
    VendorPatch,
    VendorsOut,
)
from gharbasket.platform import audit
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.geo import LatLng
from gharbasket.platform.idempotency import Idempotency, Idempotent
from gharbasket.platform.locale import Lang, Language
from gharbasket.platform.pagination import Cursor, Page, decode_cursor, encode_cursor
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["hub_ops"])

CanReadHubs = Annotated[Permit, require("hubs:read")]
CanWriteHubs = Annotated[Permit, require("hubs:write")]
CanReadVendors = Annotated[Permit, require("vendors:read")]
CanWriteVendors = Annotated[Permit, require("vendors:write")]


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


def _hub_out(hub: Hub) -> HubOut:
    area = hub.service_area_geojson
    return HubOut(
        id=hub.id,
        slug=hub.slug,
        name=hub.name,
        status=hub.status,  # pyright: ignore[reportArgumentType]
        address=hub.address,
        location=LatLng(lat=hub.lat, lng=hub.lng),
        service_area=None if area is None else GeoPolygon.model_validate_json(area),
        timings=Timings.model_validate(hub.timings),
        procurement_buffer_pct=hub.procurement_buffer_pct,
        pre_live_box_fee_paise=hub.pre_live_box_fee_paise,
        version=hub.version,
        created_at=hub.created_at,
        updated_at=hub.updated_at,
    )


def _vendor_out(vendor: Vendor) -> VendorOut:
    return VendorOut(
        id=vendor.id,
        hub_id=vendor.hub_id,
        name=vendor.name,
        kind=vendor.kind,  # pyright: ignore[reportArgumentType]
        phone=vendor.phone_e164,
        whatsapp=vendor.whatsapp,
        is_default=vendor.is_default,
        notes=vendor.notes,
        active=vendor.active,
        score=None,  # vendor scores arrive with goods receipts (§3.4)
        version=vendor.version,
        created_at=vendor.created_at,
    )


def _sees_hub(permit: Permit, hub_id: uuid.UUID) -> None:
    """Hub staff see only the hubs in their grants; any other hub does not exist for them."""
    if not permit.allows(hub_id):
        raise service.hub_not_found()


# --- hubs ---------------------------------------------------------------------------------------


@router.get(
    "/hubs",
    summary="Hubs the caller works in (admin: every hub), by name",
    responses=SIGNED_IN,
)
async def list_hubs(permit: CanReadHubs, session: DbSession, response: Response) -> HubsOut:
    unscoped = any(grant.scope_id is None for grant in permit.grants)
    ids = None if unscoped else {g.scope_id for g in permit.grants if g.scope_id is not None}
    hubs = await service.list_hubs(session, ids)
    response.headers["Cache-Control"] = "no-store"
    return HubsOut(items=[HubSummary.model_validate(hub, from_attributes=True) for hub in hubs])


@router.get(
    "/hubs/{hub_id}",
    summary="A hub with its location, service area and day timings (ETag = version)",
    responses={**SIGNED_IN, 404: _problem("hub_ops.hub_not_found")},
)
async def get_hub(
    hub_id: uuid.UUID, permit: CanReadHubs, session: DbSession, response: Response
) -> HubOut:
    _sees_hub(permit, hub_id)
    hub = await service.get_hub(session, hub_id)
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, hub.version)
    return _hub_out(hub)


@router.post(
    "/admin/hubs",
    status_code=status.HTTP_201_CREATED,
    summary="Create a hub (admin; Idempotency-Key required; audit-logged)",
    responses={
        **CREATES,
        409: _problem("hub_ops.slug_taken"),
        422: _problem(
            "hub_ops.area_invalid, hub_ops.timings_invalid, request.idempotency_mismatch, "
            "request.invalid"
        ),
    },
)
async def create_hub(
    body: HubIn, permit: CanWriteHubs, session: DbSession, idem: Idempotent, response: Response
) -> HubOut:
    hub = await service.create_hub(session, body)
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="hubs.created",
        entity_type="hub",
        entity_id=str(hub.id),
        after=body.model_dump(mode="json"),
    )
    out = _hub_out(hub)
    await idem.save(201, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.patch(
    "/admin/hubs/{hub_id}",
    summary="Change a hub (admin; If-Match: the hub's version; audit-logged)",
    responses={
        **EDITS,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("hub_ops.area_invalid, hub_ops.timings_invalid, request.invalid"),
    },
)
async def patch_hub(
    hub_id: uuid.UUID,
    body: HubPatch,
    permit: CanWriteHubs,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> HubOut:
    hub = await service.get_hub(session, hub_id, for_update=True)
    service.check_version(hub.version, expected)
    before = _hub_out(hub).model_dump(mode="json")
    changed = await service.update_hub(session, hub, body)
    out = _hub_out(hub)
    if changed:
        after = out.model_dump(mode="json")
        await audit.record(
            session,
            actor_user_id=permit.principal.user_id,
            action="hubs.updated",
            entity_type="hub",
            entity_id=str(hub.id),
            before={name: before[name] for name in changed},
            after={name: after[name] for name in changed},
        )
    await session.commit()
    set_etag(response, out.version)
    return out


# --- vendors ------------------------------------------------------------------------------------


@router.get(
    "/hubs/{hub_id}/vendors",
    summary="A hub's vendors, by name",
    responses={**SIGNED_IN, 404: _problem("hub_ops.hub_not_found")},
)
async def list_vendors(
    hub_id: uuid.UUID,
    permit: CanReadVendors,
    session: DbSession,
    response: Response,
    active: Annotated[bool | None, Query(description="Only active (or inactive) vendors")] = None,
) -> VendorsOut:
    permit.ensure(hub_id)
    hub = await service.get_hub(session, hub_id)
    vendors = await service.list_vendors(session, hub.id, active)
    response.headers["Cache-Control"] = "no-store"
    return VendorsOut(items=[_vendor_out(vendor) for vendor in vendors])


@router.post(
    "/hubs/{hub_id}/vendors",
    status_code=status.HTTP_201_CREATED,
    summary="Add a vendor to a hub (Idempotency-Key required)",
    responses={
        **CREATES,
        404: _problem("hub_ops.hub_not_found"),
        409: _problem("hub_ops.vendor_name_taken"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def create_vendor(
    hub_id: uuid.UUID,
    body: VendorIn,
    permit: CanWriteVendors,
    session: DbSession,
    idem: Idempotent,
    response: Response,
) -> VendorOut:
    permit.ensure(hub_id)
    hub = await service.get_hub(session, hub_id, for_update=True)
    out = _vendor_out(await service.create_vendor(session, hub, body))
    await idem.save(201, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.patch(
    "/vendors/{vendor_id}",
    summary="Change or deactivate a vendor (If-Match: the vendor's version)",
    responses={
        **EDITS,
        404: _problem("hub_ops.vendor_not_found"),
        409: _problem("hub_ops.vendor_name_taken"),
        422: _problem("request.invalid"),
    },
)
async def patch_vendor(
    vendor_id: uuid.UUID,
    body: VendorPatch,
    permit: CanWriteVendors,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> VendorOut:
    vendor = await service.get_vendor(session, vendor_id)
    if not permit.allows(vendor.hub_id):
        raise service.vendor_not_found()
    out = _vendor_out(await service.update_vendor(session, vendor, body, expected))
    await session.commit()
    set_etag(response, out.version)
    return out


# --- packing (§3.2) -------------------------------------------------------------------------------

CanReadPacking = Annotated[Permit, require("packing:read")]
CanPack = Annotated[Permit, require("packing:write")]
CanManagePacking = Annotated[Permit, require("packing:manage")]
Day = Annotated[date | None, Query(alias="date", description="IST date; default today")]
PACKER_MOVES: dict[int | str, dict[str, Any]] = {
    **CREATES,
    404: _problem("hub_ops.task_not_found, hub_ops.line_not_found"),
    409: _problem(
        "hub_ops.task_packed, hub_ops.task_not_started, hub_ops.task_taken, "
        "hub_ops.shortage_recorded, hub_ops.lines_open, orders.illegal_transition"
    ),
    422: _problem(
        "hub_ops.qty_invalid, hub_ops.substitute_invalid, hub_ops.bag_mismatch, "
        "request.idempotency_mismatch, request.invalid"
    ),
}


@router.get(
    "/hubs/{hub_id}/packing-tasks",
    summary="The hub's packing queue for a date, by seq",
    responses={**SIGNED_IN, 422: _problem("hub_ops.date_out_of_range, request.invalid_cursor")},
)
async def list_packing_tasks(
    hub_id: uuid.UUID,
    permit: CanReadPacking,
    session: DbSession,
    response: Response,
    day: Day = None,
    task_status: Annotated[TaskStatus | None, Query(alias="status")] = None,
    society_id: uuid.UUID | None = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> Page[PackingTaskOut]:
    permit.ensure(hub_id)
    after = None
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        if not isinstance(after, int):
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
    tasks = await packing.list_tasks(
        session,
        hub_id,
        packing.packing_day(day),
        status=task_status,
        society_id=society_id,
        after_seq=after,
        limit=limit + 1,
    )
    page = tasks[:limit]
    response.headers["Cache-Control"] = "no-store"
    return Page(
        items=await packing.views(session, page),
        next_cursor=encode_cursor({"after": page[-1].seq}) if len(tasks) > limit else None,
    )


@router.get(
    "/hubs/{hub_id}/packing-summary",
    summary="Tasks by status and bags and crates per society for a date",
    responses={**SIGNED_IN, 422: _problem("hub_ops.date_out_of_range")},
)
async def packing_summary(
    hub_id: uuid.UUID,
    permit: CanReadPacking,
    session: DbSession,
    response: Response,
    day: Day = None,
) -> PackingSummary:
    permit.ensure(hub_id)
    on = packing.packing_day(day)
    counts = await packing.status_counts(session, hub_id, on)
    response.headers["Cache-Control"] = "no-store"
    return PackingSummary(
        date=on, **counts, societies=await packing.society_loads(session, hub_id, on)
    )


@router.post(
    "/hubs/{hub_id}/packing-tasks/build",
    summary="Build the date's packing tasks now (the 05:00 job's work; Idempotency-Key)",
    responses={
        **CREATES,
        404: _problem("hub_ops.hub_not_found"),
        422: _problem("hub_ops.date_out_of_range, request.idempotency_mismatch, request.invalid"),
    },
)
async def build_packing_tasks(
    hub_id: uuid.UUID, body: BuildIn, permit: CanManagePacking, session: DbSession, idem: Idempotent
) -> BuildOut:
    permit.ensure(hub_id)
    created, existing = await packing.build(session, hub_id, packing.packing_day(body.date))
    out = BuildOut(created=created, existing=existing)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/hubs/{hub_id}/packing-tasks/next",
    response_model=PackingTaskDetail,
    summary="Claim the next task: the caller's own in picking, else the first queued one",
    responses={**PACKER_MOVES, 204: {"description": "Nothing queued"}},
)
async def next_packing_task(
    hub_id: uuid.UUID, permit: CanPack, session: DbSession, idem: Idempotent, lang: Lang
) -> PackingTaskDetail | Response:
    permit.ensure(hub_id)
    by = packing.actor(permit, hub_id)
    task = await packing.claim_next(session, hub_id, by, packing.packing_day(None))
    if task is None:
        await session.rollback()  # nothing happened, so the key is not kept either
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await _answer(session, idem, task, lang)


@router.get(
    "/packing-tasks/{task_id}",
    summary="A packing task with its lines",
    responses={**SIGNED_IN, 404: _problem("hub_ops.task_not_found")},
)
async def get_packing_task(
    task_id: uuid.UUID, permit: CanReadPacking, session: DbSession, lang: Lang, response: Response
) -> PackingTaskDetail:
    task = await packing.get_task(session, task_id, permit)
    response.headers["Cache-Control"] = "no-store"
    return await packing.detail(session, task, lang)


async def _answer(
    session: AsyncSession, idem: Idempotency, task: PackingTask, lang: Language
) -> PackingTaskDetail:
    out = await packing.detail(session, task, lang)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/packing-tasks/{task_id}/start",
    summary="Start a task (the order moves locked → picking; Idempotency-Key)",
    responses=PACKER_MOVES,
)
async def start_packing(
    task_id: uuid.UUID,
    permit: CanPack,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    body: Annotated[StartIn | None, Body()] = None,
) -> PackingTaskDetail:
    body = body or StartIn()
    task = await packing.get_task(session, task_id, permit, for_update=True)
    by = packing.actor(permit, task.hub_id)
    await packing.start(session, task, by, body.bag_qr, body.client_at)
    return await _answer(session, idem, task, lang)


@router.post(
    "/packing-tasks/{task_id}/pick",
    summary="Set the steps picked for a line (absolute; Idempotency-Key)",
    responses={**PACKER_MOVES, 403: _problem("auth.forbidden, hub_ops.not_your_task")},
)
async def pick_line(
    task_id: uuid.UUID,
    body: PickIn,
    permit: CanPack,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> PackingTaskDetail:
    task = await packing.get_task(session, task_id, permit, for_update=True)
    by = packing.actor(permit, task.hub_id)
    await packing.pick(session, task, by, body.product_id, body.picked_qty, body.client_at)
    return await _answer(session, idem, task, lang)


@router.post(
    "/packing-tasks/{task_id}/shortage",
    summary="Refund short steps, or substitute the whole line (Idempotency-Key)",
    responses={**PACKER_MOVES, 403: _problem("auth.forbidden, hub_ops.not_your_task")},
)
async def record_shortage(
    task_id: uuid.UUID,
    body: ShortageIn,
    permit: CanPack,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> PackingTaskDetail:
    task = await packing.get_task(session, task_id, permit, for_update=True)
    by = packing.actor(permit, task.hub_id)
    await packing.shortage(
        session,
        task,
        by,
        body.product_id,
        body.short_qty,
        body.substitute_product_id,
        body.client_at,
    )
    return await _answer(session, idem, task, lang)


@router.post(
    "/packing-tasks/{task_id}/complete",
    summary="Pack the bag (the order moves picking → packed; Idempotency-Key)",
    responses={**PACKER_MOVES, 403: _problem("auth.forbidden, hub_ops.not_your_task")},
)
async def complete_packing(
    task_id: uuid.UUID,
    permit: CanPack,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    body: Annotated[CompleteIn | None, Body()] = None,
) -> PackingTaskDetail:
    body = body or CompleteIn()
    task = await packing.get_task(session, task_id, permit, for_update=True)
    by = packing.actor(permit, task.hub_id)
    await packing.complete(session, task, by, body.bags, body.crate_no, body.client_at)
    return await _answer(session, idem, task, lang)


@router.post(
    "/packing-tasks/{task_id}/release",
    summary="Free a started task so another packer can take it (hub manager; Idempotency-Key)",
    responses=PACKER_MOVES,
)
async def release_packing(
    task_id: uuid.UUID,
    permit: CanManagePacking,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> PackingTaskDetail:
    task = await packing.get_task(session, task_id, permit, for_update=True)
    await packing.release(session, task, packing.actor(permit, task.hub_id))
    return await _answer(session, idem, task, lang)
