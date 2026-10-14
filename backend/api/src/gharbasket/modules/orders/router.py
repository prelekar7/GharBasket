"""HTTP endpoints of M4 Orders & Checkout: slots, orders, add-ons and cancel (spec §3.1),
problems reported on an order (§3.2), the live timeline (§3.3), the console's triage of problems
(§3.6), and paying a failed box (§3.4). Tiffins (§3.5) are in `tiffin_router`."""

import uuid
from dataclasses import asdict
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request, Response, status
from fastapi.sse import EventSourceResponse

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.api import step_label
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import issues, live, service, triage
from gharbasket.modules.orders import jobs as _jobs  # noqa: F401 (registers jobs, consumers)
from gharbasket.modules.orders import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.orders.models import Order, OrderIssue, Slot
from gharbasket.modules.orders.schemas import (
    AddonsIn,
    IssueContact,
    IssueIn,
    IssueItemOut,
    IssueKind,
    IssueOut,
    IssuePhoto,
    IssuesOut,
    IssueStatus,
    IssueTriage,
    IssueTriageDetail,
    OrderEventsOut,
    OrderLineOut,
    OrderOut,
    OrderStep,
    OrderSummaryOut,
    ProductRef,
    ResolveIn,
    SlotIn,
    SlotOut,
    SlotPatch,
    SlotRef,
    SlotsOut,
    Status,
)
from gharbasket.modules.orders.service import IST, SlotLoad
from gharbasket.modules.orders.tiffin_router import router as tiffin_router
from gharbasket.platform import audit, files
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Limit, Page
from gharbasket.platform.permissions import Permit, forbidden, require
from gharbasket.platform.valkey import Valkey

router = APIRouter(tags=["orders"])
router.include_router(tiffin_router)

CanRead = Annotated[Permit, require("orders:read")]
CanWrite = Annotated[Permit, require("orders:write")]
CanDefineSlots = Annotated[Permit, require("slots:write")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _slot(load: SlotLoad) -> SlotOut:
    s = load.slot
    return SlotOut(
        id=s.id,
        hub_id=s.hub_id,
        label=s.label,
        kind=s.kind,  # pyright: ignore[reportArgumentType]
        start=s.start_time,
        end=s.end_time,
        capacity=s.capacity,
        booked=load.booked,
        available=load.available,
    )


async def _scope(
    session: DbSession, permit: Permit, hub_id: uuid.UUID | None
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """(household_id, hub_id) a caller may read: their household, or their hub as staff."""
    if any(g.scope_type == "hub" for g in permit.grants) or any(
        g.role == "admin" for g in permit.grants
    ):
        if hub_id is None:
            raise ProblemError(422, "orders.hub_required", "Hub staff pass hub_id.")
        if not any(g.role == "admin" for g in permit.grants):
            permit.ensure(hub_id)
        return None, hub_id
    household = await identity_api.household_for_user(session, permit.principal.user_id)
    if household is None:
        raise ProblemError(404, "orders.not_found", "No household yet.")
    return household.id, None


async def _owned(
    session: DbSession, permit: Permit, order_id: uuid.UUID, *, for_update: bool = False
) -> Order:
    """The order if an admin or staff of its hub asks, or its household's owner as a customer;
    another customer's order is 404, another hub's 403 for staff without the customer role."""
    order = await service.get(session, order_id, for_update=for_update)
    if permit.allows(order.hub_id):
        return order
    if not permit.has_role("customer"):
        raise forbidden()
    household = await identity_api.household_for_user(session, permit.principal.user_id)
    if household is None or household.id != order.household_id:
        raise service.order_not_found()
    return order


async def _summary(session: DbSession, order: Order, slot: Slot | None = None) -> OrderSummaryOut:
    slot = slot or await service.get_slot(session, order.slot_id)
    return OrderSummaryOut(
        id=order.id,
        kind=order.kind,  # pyright: ignore[reportArgumentType]
        status=order.status,  # pyright: ignore[reportArgumentType]
        cycle_id=order.cycle_id,
        household_id=order.household_id,
        hub_id=order.hub_id,
        society_id=order.society_id,
        delivery_date=order.delivery_date,
        slot=SlotRef(id=slot.id, label=slot.label, start=slot.start_time, end=slot.end_time),
        cutoff_at=order.cutoff_at,
        subtotal_paise=order.subtotal_paise,
        fees_paise=order.fees_paise,
        discount_paise=order.discount_paise,
        total_paise=order.total_paise,
        version=order.version,
        created_at=order.created_at,
        locked_at=order.locked_at,
    )


async def _full(session: DbSession, order: Order, lang: Language) -> OrderOut:
    lines = await service.lines_of(session, order.id)
    ids = {line.product_id for line in lines} | {
        line.substituted_from for line in lines if line.substituted_from is not None
    }
    products = await catalog_api.line_products(session, list(ids))
    out_lines = []
    for line in lines:
        product = products.get(line.product_id)
        substituted = products.get(line.substituted_from) if line.substituted_from else None
        out_lines.append(
            OrderLineOut(
                product_id=line.product_id,
                slug=product.slug if product else "",
                name=localised(product.names, lang) if product else "",
                kind=line.kind,  # pyright: ignore[reportArgumentType]
                qty=line.qty,
                step=OrderStep(
                    qty=line.step_qty,
                    unit=line.step_unit,  # pyright: ignore[reportArgumentType]
                    label=step_label(line.step_qty, line.step_unit, lang),
                ),
                unit_price_paise=line.unit_price_paise,
                total_paise=line.total_paise,
                reason_code=line.reason_code,
                reason_params=line.reason_params,
                substituted_from=None
                if substituted is None
                else ProductRef(
                    product_id=substituted.id,
                    slug=substituted.slug,
                    name=localised(substituted.names, lang),
                ),
                shortage_qty=line.shortage_qty,
            )
        )
    timeline = [live.entry(e) for e in await service.timeline_of(session, order.id)]
    summary = await _summary(session, order)
    return OrderOut(**summary.model_dump(), lines=out_lines, timeline=timeline)


# --- slots --------------------------------------------------------------------------------------


@router.get(
    "/slots",
    summary="Delivery slots of a hub on a date, with availability",
    responses={422: _problem("orders.date_out_of_range, orders.hub_required")},
)
async def list_slots(
    permit: CanRead,
    session: DbSession,
    response: Response,
    hub_id: Annotated[
        uuid.UUID | None, Query(description="Default: the caller's household's hub")
    ] = None,
    on: Annotated[date | None, Query(alias="date", description="Default: tomorrow (IST)")] = None,
) -> SlotsOut:
    hub = hub_id or await identity_api.household_hub(session, permit.principal.user_id)
    if hub is None:
        raise ProblemError(422, "orders.hub_required", "Pass hub_id, or onboard a household first.")
    on = on or (service.now().astimezone(IST).date() + timedelta(days=1))
    loads = await service.slots_on(session, hub, on)
    response.headers["Cache-Control"] = "no-store"
    return SlotsOut(date=on, items=[_slot(load) for load in loads])


@router.post(
    "/admin/slots",
    status_code=status.HTTP_201_CREATED,
    summary="Define a delivery slot for a hub (hub manager of that hub, or admin; audit-logged)",
    responses={
        403: _problem("auth.forbidden"),
        409: _problem("orders.slot_exists"),
        422: _problem("orders.slot_invalid"),
    },
)
async def create_slot(
    body: SlotIn, permit: CanDefineSlots, session: DbSession, idem: Idempotent
) -> SlotOut:
    permit.ensure(body.hub_id)
    slot = await service.create_slot(
        session,
        hub_id=body.hub_id,
        label=body.label,
        kind=body.kind,
        start=body.start,
        end=body.end,
        capacity=body.capacity,
    )
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="slots.created",
        entity_type="slot",
        entity_id=str(slot.id),
        after=body.model_dump(mode="json"),
    )
    out = _slot(SlotLoad(slot, 0))
    await idem.save(201, out)
    await session.commit()
    return out


@router.patch(
    "/admin/slots/{slot_id}",
    summary="Change a slot's capacity or close it",
    responses={403: _problem("auth.forbidden"), 422: _problem("orders.slot_invalid")},
)
async def patch_slot(
    slot_id: uuid.UUID, body: SlotPatch, permit: CanDefineSlots, session: DbSession
) -> SlotOut:
    slot = await service.get_slot(session, slot_id, for_update=True)
    permit.ensure(slot.hub_id)
    before = {"capacity": slot.capacity, "active": slot.active}
    if body.capacity is not None:
        slot.capacity = body.capacity
    if body.active is not None:
        slot.active = body.active
    await session.flush()
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="slots.updated",
        entity_type="slot",
        entity_id=str(slot.id),
        before=before,
        after={"capacity": slot.capacity, "active": slot.active},
    )
    loads = {
        load.slot.id: load
        for load in await service.slots_on(
            session, slot.hub_id, service.now().astimezone(IST).date()
        )
    }
    booked = loads[slot.id].booked if slot.id in loads else 0
    await session.commit()
    return _slot(SlotLoad(slot, booked))


# --- orders -------------------------------------------------------------------------------------


@router.get(
    "/orders",
    summary="Orders of the caller's household, or of a hub for its staff (newest delivery first)",
    responses={
        404: _problem("orders.not_found: the caller has no household yet"),
        422: _problem("orders.hub_required, request.invalid_cursor"),
    },
)
async def list_orders(
    permit: CanRead,
    session: DbSession,
    response: Response,
    order_status: Annotated[Status | None, Query(alias="status")] = None,
    from_date: Annotated[date | None, Query(alias="from")] = None,
    to_date: Annotated[date | None, Query(alias="to")] = None,
    hub_id: uuid.UUID | None = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[OrderSummaryOut]:
    household_id, hub = await _scope(session, permit, hub_id)
    orders, next_cursor = await service.list_orders(
        session,
        household_id=household_id,
        hub_id=hub,
        status=order_status,
        from_date=from_date,
        to_date=to_date,
        cursor=cursor,
        limit=limit,
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=[await _summary(session, o) for o in orders], next_cursor=next_cursor)


@router.get(
    "/orders/{order_id}",
    summary="One order with its lines and timeline (ETag = version)",
    responses={404: _problem("orders.not_found")},
)
async def get_order(
    order_id: uuid.UUID, permit: CanRead, session: DbSession, lang: Lang, response: Response
) -> OrderOut:
    order = await _owned(session, permit, order_id)
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, order.version)
    return await _full(session, order, lang)


@router.get(
    "/orders/{order_id}/events",
    summary="The order's live timeline: JSON for polling clients, or a Server-Sent Events stream",
    description="`Accept: text/event-stream` streams `snapshot`, then `status` per new timeline "
    "row, `: ping` every 20 s and `end` at a final status (delivered, cancelled, refunded); "
    "it closes after 15 minutes. Reconnect with `Last-Event-ID` (a timeline row id) for the rows "
    "after it, without a new snapshot. Any other Accept answers JSON, oldest first.",
    response_model=OrderEventsOut,
    responses={
        200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
        403: _problem("auth.forbidden: hub staff of another hub"),
        404: _problem("orders.not_found"),
        429: _problem("orders.too_many_streams: more than 3 open streams for this user"),
    },
)
async def order_events(
    order_id: uuid.UUID,
    permit: CanRead,
    session: DbSession,
    valkey: Valkey,
    request: Request,
    response: Response,
    last_event_id: Annotated[str | None, Header()] = None,
) -> OrderEventsOut | Response:
    order = await _owned(session, permit, order_id)
    if "text/event-stream" not in request.headers.get("accept", ""):
        response.headers["Cache-Control"] = "no-store"
        return await live.history(session, order.id)
    after = await live.resume_point(session, order.id, last_event_id)
    user_id = permit.principal.user_id
    body = live.stream(session, order.id, after)
    if await live.claim(valkey, user_id):
        body = live.counted(body, valkey, user_id)
    return EventSourceResponse(
        body, headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    )


@router.post(
    "/orders/{order_id}/addons",
    summary="Add, change or remove add-on lines before the cut-off (Idempotency-Key, If-Match)",
    responses={
        404: _problem("orders.not_found"),
        409: _problem("orders.cutoff_passed, orders.not_editable"),
        412: _problem("request.version_mismatch"),
        422: _problem("orders.product_unknown, orders.qty_invalid, orders.no_price"),
        428: _problem("request.precondition_required"),
    },
)
async def set_addons(
    order_id: uuid.UUID,
    body: AddonsIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> OrderOut:
    order = await _owned(session, permit, order_id, for_update=True)
    items: dict[uuid.UUID, int] = {}
    for item in body.items:
        items[item.product_id] = item.qty
    await service.set_addons(session, order, items, expected_version=expected)
    out = await _full(session, order, lang)
    await idem.save(200, out)
    await session.commit()
    set_etag(response, order.version)
    return out


@router.post(
    "/orders/{order_id}/cancel",
    summary="Cancel before the cut-off (Idempotency-Key); nothing was paid yet",
    responses={
        404: _problem("orders.not_found"),
        409: _problem("orders.cutoff_passed, orders.already_locked"),
    },
)
async def cancel_order(
    order_id: uuid.UUID, permit: CanWrite, session: DbSession, idem: Idempotent, lang: Lang
) -> OrderOut:
    order = await _owned(session, permit, order_id, for_update=True)
    await service.cancel(
        session,
        order,
        actor="customer",
        reason="customer",
        actor_user_id=permit.principal.user_id,
    )
    out = await _full(session, order, lang)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/orders/{order_id}/pay",
    summary="Pay a payment_failed box from the wallet before its grace ends (Idempotency-Key)",
    description="The same debit as the cut-off (one per order, never twice), then `locked`. "
    "An order that is already locked (a top-up paid it) answers as it is.",
    responses={
        404: _problem("orders.not_found"),
        409: _problem(
            "billing.insufficient_balance (balance_paise, needed_paise), orders.not_payable, "
            "orders.payment_grace_over"
        ),
    },
)
async def pay_order(
    order_id: uuid.UUID,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    response: Response,
) -> OrderOut:
    order = await _owned(session, permit, order_id, for_update=True)
    await service.pay(session, order)
    out = await _full(session, order, lang)
    await idem.save(200, out)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, order.version)
    return out


# --- issues (§3.2) ------------------------------------------------------------------------------


async def _issues_out(
    session: DbSession, found: list[OrderIssue], lang: Language
) -> list[IssueOut]:
    items = await issues.items_of(session, [issue.id for issue in found])
    ids = list({item.product_id for lines in items.values() for item in lines})
    products = await catalog_api.line_products(session, ids)
    out = []
    for issue in found:
        lines = []
        for item in items[issue.id]:
            product = products.get(item.product_id)
            lines.append(
                IssueItemOut(
                    product_id=item.product_id,
                    slug=product.slug if product else "",
                    name=localised(product.names, lang) if product else "",
                    qty=item.qty,
                    unit_price_paise=item.unit_price_paise,
                    amount_paise=item.qty * item.unit_price_paise,
                )
            )
        out.append(IssueOut(**issues.summary(issue), items=lines))
    return out


@router.post(
    "/orders/{order_id}/issues",
    status_code=status.HTTP_201_CREATED,
    summary="Report a problem with a delivery (Idempotency-Key); auto-credit or triage",
    responses={
        404: _problem("orders.not_found"),
        409: _problem("orders.issue_window_closed, orders.too_many_issues"),
        422: _problem(
            "orders.issue_kind_invalid, orders.issue_items_required, orders.issue_item_unknown "
            "(with product_ids), orders.issue_qty_invalid, orders.photo_key_invalid"
        ),
        503: _problem("platform.storage_unavailable (only when photo_keys are sent)"),
    },
)
async def report_issue(
    order_id: uuid.UUID,
    body: IssueIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    request: Request,
) -> IssueOut:
    order = await _owned(session, permit, order_id, for_update=True)
    wanted: dict[uuid.UUID, int] = {}
    for item in body.items or []:
        wanted[item.product_id] = wanted.get(item.product_id, 0) + item.qty
    issue = await issues.report(
        session,
        order,
        kind=body.kind,
        items=wanted,
        note=body.note,
        photo_keys=body.photo_keys,
        user_id=permit.principal.user_id,
        store=getattr(request.app.state, "storage", None),
    )
    out = (await _issues_out(session, [issue], lang))[0]
    await idem.save(201, out)
    await session.commit()
    return out


@router.get(
    "/orders/{order_id}/issues",
    summary="Problems reported on an order, newest first (the owner, or hub staff)",
    responses={404: _problem("orders.not_found")},
)
async def list_issues(
    order_id: uuid.UUID, permit: CanRead, session: DbSession, lang: Lang, response: Response
) -> IssuesOut:
    order = await _owned(session, permit, order_id)
    response.headers["Cache-Control"] = "no-store"
    return IssuesOut(
        items=await _issues_out(session, await issues.of_order(session, order.id), lang)
    )


# --- triage (§3.6) ------------------------------------------------------------------------------

CanTriage = Annotated[Permit, require("order_issues:resolve")]


async def _triage_out(
    session: DbSession, found: list[OrderIssue], lang: Language
) -> list[IssueTriage]:
    known = await triage.facts(session, found)
    return [
        IssueTriage(
            **out.model_dump(),
            household_id=issue.household_id,
            hub_id=issue.hub_id,
            **asdict(known[issue.id]),
        )
        for out, issue in zip(await _issues_out(session, found, lang), found, strict=True)
    ]


async def _audit_read(
    session: DbSession, permit: Permit, issue: OrderIssue, action: str, after: dict[str, Any]
) -> None:
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action=action,
        entity_type="order_issue",
        entity_id=str(issue.id),
        after=after,
    )


@router.get(
    "/admin/order-issues",
    summary="The triage queue of a hub (all hubs for admins), oldest first",
    responses={
        403: _problem("auth.forbidden: another hub"),
        422: _problem("orders.hub_required (hub staff), request.invalid_cursor"),
    },
)
async def triage_queue(
    permit: CanTriage,
    session: DbSession,
    lang: Lang,
    response: Response,
    hub_id: Annotated[uuid.UUID | None, Query(description="Required for hub staff")] = None,
    issue_status: Annotated[IssueStatus, Query(alias="status")] = "open",
    kind: IssueKind | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[IssueTriage]:
    if hub_id is not None:
        permit.ensure(hub_id)
    elif not any(g.role == "admin" for g in permit.grants):
        raise ProblemError(422, "orders.hub_required", "Hub staff pass hub_id.")
    found, next_cursor = await triage.queue(
        session, hub_id=hub_id, status=issue_status, kind=kind, cursor=cursor, limit=limit
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=await _triage_out(session, found, lang), next_cursor=next_cursor)


@router.get(
    "/admin/order-issues/{issue_id}",
    summary="One issue with 5-minute photo links and the customer's contact (audit-logged)",
    responses={
        403: _problem("auth.forbidden: another hub"),
        404: _problem("orders.issue_not_found"),
    },
)
async def triage_detail(
    issue_id: uuid.UUID,
    permit: CanTriage,
    session: DbSession,
    lang: Lang,
    request: Request,
    response: Response,
) -> IssueTriageDetail:
    issue = await triage.get(session, issue_id)
    permit.ensure(issue.hub_id)
    store: files.ObjectStore | None = getattr(request.app.state, "storage", None)
    photos: list[IssuePhoto] = []
    if store is not None:  # none while storage is down; photo_count still says how many
        for key in issue.photo_keys:
            url = store.signed_get(key)
            photos.append(IssuePhoto(url=url, expires_at=service.now() + files.GET_TTL))
            await _audit_read(session, permit, issue, "order_issues.photo_link", {"key": key})
    found = await identity_api.contact_for_household(session, issue.household_id)
    contact = None if found is None else IssueContact(name=found.name, phone_e164=found.phone_e164)
    await _audit_read(
        session,
        permit,
        issue,
        "order_issues.contact_read",
        {"household_id": str(issue.household_id)},
    )
    out = (await _triage_out(session, [issue], lang))[0]
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return IssueTriageDetail(**out.model_dump(), photos=photos, contact=contact)


@router.post(
    "/admin/order-issues/{issue_id}/resolve",
    summary="Credit an open issue to the wallet or reject it (Idempotency-Key; audit-logged)",
    responses={
        403: _problem("auth.forbidden: another hub"),
        404: _problem("orders.issue_not_found"),
        409: _problem("orders.issue_not_open"),
        422: _problem(
            "orders.issue_credit_invalid (with max_paise), billing.wallet_limit (with room_paise; "
            "the issue stays open)"
        ),
    },
)
async def resolve_issue(
    issue_id: uuid.UUID,
    body: ResolveIn,
    permit: CanTriage,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> IssueTriage:
    permit.ensure(await triage.hub_of(session, issue_id))
    issue = await issues.resolve(
        session,
        issue_id,
        body.outcome,
        body.credit_paise or 0,
        permit.principal.user_id,
        body.note,
    )
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="order_issues.resolved",
        entity_type="order_issue",
        entity_id=str(issue.id),
        before={"status": "open"},
        after={"status": issue.status, "credit_paise": issue.credit_paise},
    )
    out = (await _triage_out(session, [issue], lang))[0]
    await idem.save(200, out)
    await session.commit()
    return out
