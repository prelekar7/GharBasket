"""HTTP endpoints of M6 Hub Operations: hubs, service areas and vendors (spec §3.1), procurement
plans and purchase orders (§3.3), goods receipts (§3.4)."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status

from gharbasket.modules.hub_ops import jobs as _jobs  # noqa: F401 (registers jobs)
from gharbasket.modules.hub_ops import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.hub_ops import procurement, purchasing, receiving, service
from gharbasket.modules.hub_ops.models import Hub, ProcurementPlan, PurchaseOrder, Vendor
from gharbasket.modules.hub_ops.schemas import (
    CancelIn,
    GeneratedPurchaseOrders,
    GeoPolygon,
    GoodsReceiptOut,
    GoodsReceiptsOut,
    HubIn,
    HubOut,
    HubPatch,
    HubsOut,
    HubSummary,
    PlanBuildIn,
    ProcurementLinePatch,
    ProcurementPlanOut,
    PurchaseOrderOut,
    PurchaseOrdersOut,
    ReceiptIn,
    Timings,
    VendorIn,
    VendorOut,
    VendorPatch,
    VendorsOut,
)
from gharbasket.platform import audit
from gharbasket.platform.db import DbSession
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.geo import LatLng
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["hub_ops"])

CanReadHubs = Annotated[Permit, require("hubs:read")]
CanWriteHubs = Annotated[Permit, require("hubs:write")]
CanReadVendors = Annotated[Permit, require("vendors:read")]
CanWriteVendors = Annotated[Permit, require("vendors:write")]
CanReadProcurement = Annotated[Permit, require("procurement:read")]
CanWriteProcurement = Annotated[Permit, require("procurement:write")]
CanReadInventory = Annotated[Permit, require("inventory:read")]
CanReceive = Annotated[Permit, require("receiving:write")]
Day = Annotated[date | None, Query(alias="date", description="IST delivery date; default today")]


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
    day: Day = None,
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
    day: Day = None,
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
    day: Day = None,
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
