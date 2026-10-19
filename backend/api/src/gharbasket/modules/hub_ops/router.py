"""HTTP endpoints of M6 Hub Operations: hubs, service areas and vendors (spec §3.1), packing
(§3.2), procurement plans and purchase orders (§3.3), goods receipts, lots, inventory and counts
(§3.4)."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Body, Query, Response, status
from pydantic import AwareDatetime
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import jobs as _jobs  # noqa: F401 (registers the jobs)
from gharbasket.modules.hub_ops import (
    packing,
    procurement,
    purchasing,
    receiving,
    scores,
    service,
    stock,
)
from gharbasket.modules.hub_ops import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.hub_ops.models import (
    Hub,
    InventoryLot,
    PackingTask,
    ProcurementPlan,
    PurchaseOrder,
    Vendor,
)
from gharbasket.modules.hub_ops.schemas import (
    BuildIn,
    BuildOut,
    CancelIn,
    CompleteIn,
    CountIn,
    GeneratedPurchaseOrders,
    GeoPolygon,
    GoodsReceiptOut,
    GoodsReceiptsOut,
    HubIn,
    HubOut,
    HubPatch,
    HubsOut,
    HubSummary,
    InventoryOut,
    LotDetailOut,
    LotOut,
    PackingSummary,
    PackingTaskDetail,
    PackingTaskOut,
    PickIn,
    PlanBuildIn,
    ProcurementLinePatch,
    ProcurementPlanOut,
    PurchaseOrderOut,
    PurchaseOrdersOut,
    ReceiptIn,
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
CanReadProcurement = Annotated[Permit, require("procurement:read")]
CanWriteProcurement = Annotated[Permit, require("procurement:write")]
PlanDay = Annotated[
    date | None, Query(alias="date", description="IST delivery date; default today")
]
CanReadInventory = Annotated[Permit, require("inventory:read")]
CanReceive = Annotated[Permit, require("receiving:write")]
CanManageInventory = Annotated[Permit, require("inventory:manage")]


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


def _vendor_out(vendor: Vendor, score: float | None = None) -> VendorOut:
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
        score=score,
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
    ids = (
        None if permit.is_global else {g.scope_id for g in permit.grants if g.scope_id is not None}
    )
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
    found = await scores.latest(session, [vendor.id for vendor in vendors])
    return VendorsOut(items=[_vendor_out(vendor, found.get(vendor.id)) for vendor in vendors])


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
    vendor = await service.update_vendor(session, vendor, body, expected)
    out = _vendor_out(vendor, (await scores.latest(session, [vendor.id])).get(vendor.id))
    await session.commit()
    set_etag(response, out.version)
    return out


# --- procurement plans (§3.3) -------------------------------------------------------------------


async def _plan(
    session: DbSession, permit: Permit, plan_id: uuid.UUID, *, for_update: bool = False
) -> ProcurementPlan:
    """A plan of a hub the caller works in; any other does not exist for them."""
    plan = await procurement.get_plan(session, plan_id=plan_id, for_update=for_update)
    if not permit.allows(plan.hub_id):
        raise procurement.plan_not_found()
    return plan


async def _po(
    session: DbSession, permit: Permit, po_id: uuid.UUID, *, for_update: bool = False
) -> PurchaseOrder:
    po = await purchasing.get_po(session, po_id)
    if not permit.allows(po.hub_id):
        raise purchasing.po_not_found()
    if for_update:  # hub, then plan, then PO: the order every procurement write locks in
        await service.get_hub(session, po.hub_id, for_update=True)
        await procurement.get_plan(session, plan_id=po.plan_id, for_update=True)
        po = await purchasing.get_po(session, po_id, for_update=True)
    return po


async def _po_out(session: DbSession, po: PurchaseOrder, lang: Lang) -> PurchaseOrderOut:
    return (await purchasing.pos_out(session, [po], lang))[0]


@router.get(
    "/hubs/{hub_id}/procurement-plans",
    summary="The hub's procurement plan for a delivery date (ETag = version)",
    responses={
        **SIGNED_IN,
        404: _problem("hub_ops.plan_not_found (not built yet)"),
        422: _problem("hub_ops.date_out_of_range"),
    },
)
async def get_hub_plan(
    hub_id: uuid.UUID,
    permit: CanReadProcurement,
    session: DbSession,
    response: Response,
    lang: Lang,
    day: PlanDay = None,
) -> ProcurementPlanOut:
    permit.ensure(hub_id)
    plan = await procurement.get_plan(session, hub_id=hub_id, day=procurement.plan_day(day))
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, plan.version)
    return await procurement.plan_out(session, plan, lang)


@router.get(
    "/procurement-plans/{plan_id}",
    summary="A procurement plan with its lines (ETag = version)",
    responses={**SIGNED_IN, 404: _problem("hub_ops.plan_not_found")},
)
async def get_plan(
    plan_id: uuid.UUID,
    permit: CanReadProcurement,
    session: DbSession,
    response: Response,
    lang: Lang,
) -> ProcurementPlanOut:
    plan = await _plan(session, permit, plan_id)
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, plan.version)
    return await procurement.plan_out(session, plan, lang)


@router.post(
    "/hubs/{hub_id}/procurement-plans/build",
    summary="Build or rebuild the hub's plan for a date now (the 21:10 job's work)",
    responses={
        **CREATES,
        404: _problem("hub_ops.hub_not_found"),
        409: _problem("hub_ops.plan_not_editable"),
        422: _problem("hub_ops.date_out_of_range, request.idempotency_mismatch, request.invalid"),
    },
)
async def build_plan(
    hub_id: uuid.UUID,
    body: PlanBuildIn,
    permit: CanWriteProcurement,
    session: DbSession,
    idem: Idempotent,
    response: Response,
    lang: Lang,
) -> ProcurementPlanOut:
    permit.ensure(hub_id)
    plan, outcome = await procurement.build(
        session, hub_id, procurement.plan_day(body.day, earliest=0)
    )
    if plan is None or outcome == "closed":
        raise procurement.plan_not_editable()
    out = await procurement.plan_out(session, plan, lang)
    await idem.save(200, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.patch(
    "/procurement-plans/{plan_id}/lines/{product_id}",
    summary="Change a plan line (If-Match: the plan's version; a reason for a quantity; audited)",
    responses={
        **EDITS,
        404: _problem("hub_ops.plan_not_found, hub_ops.line_not_found, hub_ops.vendor_not_found"),
        409: _problem("hub_ops.plan_not_editable, hub_ops.line_ordered"),
        422: _problem(
            "hub_ops.qty_invalid, hub_ops.reason_required, hub_ops.vendor_inactive, request.invalid"
        ),
    },
)
async def patch_plan_line(
    plan_id: uuid.UUID,
    product_id: uuid.UUID,
    body: ProcurementLinePatch,
    permit: CanWriteProcurement,
    session: DbSession,
    expected: IfMatch,
    response: Response,
    lang: Lang,
) -> ProcurementPlanOut:
    plan = await _plan(session, permit, plan_id, for_update=True)
    await procurement.edit_line(
        session,
        plan,
        product_id,
        body,
        expected_version=expected,
        actor_user_id=permit.principal.user_id,
    )
    out = await procurement.plan_out(session, plan, lang)
    await session.commit()
    set_etag(response, out.version)
    return out


# --- purchase orders (§3.3) ---------------------------------------------------------------------


@router.post(
    "/procurement-plans/{plan_id}/purchase-orders",
    summary="(Re)generate the plan's draft purchase orders, one per vendor",
    responses={
        **CREATES,
        404: _problem("hub_ops.plan_not_found"),
        409: _problem("hub_ops.plan_not_editable, hub_ops.nothing_to_order"),
        422: _problem("hub_ops.vendor_missing (product_ids), request.idempotency_mismatch"),
    },
)
async def generate_purchase_orders(
    plan_id: uuid.UUID,
    permit: CanWriteProcurement,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> GeneratedPurchaseOrders:
    plan = await _plan(session, permit, plan_id)
    await service.get_hub(session, plan.hub_id, for_update=True)
    plan = await _plan(session, permit, plan_id, for_update=True)
    await purchasing.generate(session, plan, permit.principal.user_id)
    pos = await purchasing.pos_out(session, await purchasing.plan_pos(session, plan.id), lang)
    out = GeneratedPurchaseOrders(purchase_orders=pos)
    await idem.save(200, out)
    await session.commit()
    return out


@router.get(
    "/hubs/{hub_id}/purchase-orders",
    summary="The hub's purchase orders for a delivery date, by vendor name",
    responses={**SIGNED_IN, 422: _problem("hub_ops.date_out_of_range")},
)
async def list_purchase_orders(
    hub_id: uuid.UUID,
    permit: CanReadProcurement,
    session: DbSession,
    response: Response,
    lang: Lang,
    day: PlanDay = None,
    po_status: Annotated[
        Literal["draft", "sent", "partly_received", "received", "cancelled"] | None,
        Query(alias="status"),
    ] = None,
) -> PurchaseOrdersOut:
    permit.ensure(hub_id)
    pos = await purchasing.hub_pos(session, hub_id, procurement.plan_day(day), po_status)
    response.headers["Cache-Control"] = "no-store"
    return PurchaseOrdersOut(items=await purchasing.pos_out(session, pos, lang))


@router.get(
    "/purchase-orders/{po_id}",
    summary="A purchase order with its share text and WhatsApp link (ETag = version)",
    responses={**SIGNED_IN, 404: _problem("hub_ops.po_not_found")},
)
async def get_purchase_order(
    po_id: uuid.UUID, permit: CanReadProcurement, session: DbSession, response: Response, lang: Lang
) -> PurchaseOrderOut:
    po = await _po(session, permit, po_id)
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, po.version)
    return await _po_out(session, po, lang)


@router.post(
    "/purchase-orders/{po_id}/send",
    summary="Mark a draft as sent now (gives its number; nothing is sent automatically)",
    responses={
        **CREATES,
        404: _problem("hub_ops.po_not_found"),
        409: _problem("hub_ops.po_not_draft, hub_ops.po_stale"),
        422: _problem("hub_ops.vendor_inactive, request.idempotency_mismatch"),
    },
)
async def send_purchase_order(
    po_id: uuid.UUID,
    permit: CanWriteProcurement,
    session: DbSession,
    idem: Idempotent,
    response: Response,
    lang: Lang,
) -> PurchaseOrderOut:
    po = await _po(session, permit, po_id, for_update=True)
    await purchasing.send(session, po, permit.principal.user_id)
    out = await _po_out(session, po, lang)
    await idem.save(200, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.post(
    "/purchase-orders/{po_id}/cancel",
    summary="Cancel a draft, or a sent purchase order with nothing received",
    responses={
        **CREATES,
        404: _problem("hub_ops.po_not_found"),
        409: _problem("hub_ops.po_not_cancellable"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def cancel_purchase_order(
    po_id: uuid.UUID,
    body: CancelIn,
    permit: CanWriteProcurement,
    session: DbSession,
    idem: Idempotent,
    response: Response,
    lang: Lang,
) -> PurchaseOrderOut:
    po = await _po(session, permit, po_id, for_update=True)
    await purchasing.cancel(session, po, body.reason)
    out = await _po_out(session, po, lang)
    await idem.save(200, out)
    await session.commit()
    set_etag(response, out.version)
    return out


# --- goods receipts (§3.4) ----------------------------------------------------------------------


@router.post(
    "/hubs/{hub_id}/goods-receipts",
    status_code=status.HTTP_201_CREATED,
    summary="Record goods received at the dock, against a sent purchase order or ad hoc",
    responses={
        **CREATES,
        404: _problem("hub_ops.hub_not_found, hub_ops.po_not_found, hub_ops.vendor_not_found"),
        409: _problem("hub_ops.po_not_receivable"),
        422: _problem(
            "hub_ops.receipt_invalid (reason), hub_ops.product_not_stocked (product_ids), "
            "hub_ops.qty_invalid, hub_ops.vendor_inactive, request.idempotency_mismatch, "
            "request.invalid"
        ),
    },
)
async def create_goods_receipt(
    hub_id: uuid.UUID,
    body: ReceiptIn,
    permit: CanReceive,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> GoodsReceiptOut:
    permit.ensure(hub_id)
    receipt = await receiving.receive(session, hub_id, body, permit.principal.user_id)
    out = (await receiving.receipts_out(session, [receipt], lang))[0]
    await idem.save(201, out)
    await session.commit()
    return out


@router.get(
    "/hubs/{hub_id}/goods-receipts",
    summary="The hub's goods receipts of an IST day, newest first",
    responses=SIGNED_IN,
)
async def list_goods_receipts(
    hub_id: uuid.UUID,
    permit: CanReadInventory,
    session: DbSession,
    response: Response,
    lang: Lang,
    day: PlanDay = None,
) -> GoodsReceiptsOut:
    permit.ensure(hub_id)
    receipts = await receiving.hub_receipts(
        session, hub_id, day or datetime.now(procurement.IST).date()
    )
    response.headers["Cache-Control"] = "no-store"
    return GoodsReceiptsOut(items=await receiving.receipts_out(session, receipts, lang))


@router.get(
    "/goods-receipts/{receipt_id}",
    summary="A goods receipt with its lines and lots",
    responses={**SIGNED_IN, 404: _problem("hub_ops.receipt_not_found")},
)
async def get_goods_receipt(
    receipt_id: uuid.UUID,
    permit: CanReadInventory,
    session: DbSession,
    response: Response,
    lang: Lang,
) -> GoodsReceiptOut:
    receipt = await receiving.get_receipt(session, receipt_id)
    if not permit.allows(receipt.hub_id):
        raise receiving.receipt_not_found()
    response.headers["Cache-Control"] = "no-store"
    return (await receiving.receipts_out(session, [receipt], lang))[0]


# --- lots, inventory and counts (§3.4) ----------------------------------------------------------


async def _lot(
    session: DbSession, permit: Permit, lot_id: uuid.UUID, *, for_update: bool = False
) -> InventoryLot:
    lot = await stock.get_lot(session, lot_id)
    if not permit.allows(lot.hub_id):
        raise stock.lot_not_found()
    return await stock.get_lot(session, lot_id, for_update=True) if for_update else lot


@router.get(
    "/hubs/{hub_id}/inventory",
    summary="The hub's stock by product: lots with stock, first expiry first",
    responses=SIGNED_IN,
)
async def get_inventory(
    hub_id: uuid.UUID,
    permit: CanReadInventory,
    session: DbSession,
    response: Response,
    lang: Lang,
    product_id: uuid.UUID | None = None,
    expiring_before: Annotated[
        AwareDatetime | None, Query(description="Only lots whose best_before is earlier")
    ] = None,
) -> InventoryOut:
    permit.ensure(hub_id)
    response.headers["Cache-Control"] = "no-store"
    return await stock.inventory(
        session, hub_id, lang, product_id=product_id, expiring_before=expiring_before
    )


@router.get(
    "/lots/{lot_id}",
    summary="A lot with its stock moves",
    responses={**SIGNED_IN, 404: _problem("hub_ops.lot_not_found")},
)
async def get_lot(
    lot_id: uuid.UUID, permit: CanReadInventory, session: DbSession, response: Response, lang: Lang
) -> LotDetailOut:
    lot = await _lot(session, permit, lot_id)
    response.headers["Cache-Control"] = "no-store"
    return await stock.lot_detail(session, lot, lang)


@router.post(
    "/lots/{lot_id}/count",
    summary="Set a lot's stock after a physical count (a count move; audited)",
    responses={
        **CREATES,
        404: _problem("hub_ops.lot_not_found"),
        422: _problem("hub_ops.qty_invalid, request.idempotency_mismatch, request.invalid"),
    },
)
async def count_lot(
    lot_id: uuid.UUID,
    body: CountIn,
    permit: CanManageInventory,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> LotOut:
    lot = await _lot(session, permit, lot_id, for_update=True)
    await stock.count(session, lot, body, permit.principal.user_id)
    out = (await stock.lots_out(session, [lot], lang))[0]
    await idem.save(200, out)
    await session.commit()
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
