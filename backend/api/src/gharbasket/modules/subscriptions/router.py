"""HTTP endpoints of M3 Plans & Subscriptions (spec §3.1): plans, the subscription, delivery days,
the weekly cycle (read, edit, swap a meal (§3.2), approve, skip, un-skip (§3.3), green slots
(§3.6)), pantry feedback and the Budget Lock."""

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import Field

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import cutoff, green, planning, service
from gharbasket.modules.subscriptions import jobs as _jobs  # noqa: F401 (registers the job)
from gharbasket.modules.subscriptions import permissions as _permissions  # noqa: F401
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    Plan,
    SlotQuote,
    Subscription,
    WeeklyCycle,
)
from gharbasket.modules.subscriptions.schemas import (
    BudgetLockIn,
    BudgetLockOut,
    CaptainOut,
    CycleLineOut,
    CycleOut,
    CycleStep,
    DayOut,
    DeliveryDayOut,
    DeliveryDaysIn,
    DeliveryDaysOut,
    FeedbackIn,
    FeedbackOut,
    GreenSlotOut,
    HighlightOut,
    HubCutoffOut,
    HubDeliveryDatesOut,
    LinesPatch,
    MealOut,
    MealPlanOut,
    MealSet,
    PaymentDueOut,
    PlanOut,
    PlansOut,
    ReplacedOut,
    SlotMoveIn,
    SlotOptionOut,
    SlotQuoteOut,
    SlotRefOut,
    SubscriptionIn,
    SubscriptionOut,
    SubscriptionPatch,
)
from gharbasket.modules.subscriptions.service import IST_DATE
from gharbasket.platform import audit
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["subscriptions"])

CanRead = Annotated[Permit, require("cycles:read")]
CanWrite = Annotated[Permit, require("cycles:write")]
CanSubscribe = Annotated[Permit, require("subscriptions:write")]
CanSetDays = Annotated[Permit, require("delivery_days:write")]
CanSeeDates = Annotated[Permit, require("delivery_days:read")]
# Calendar dates the console can ask for; the day arithmetic around them never leaves the range.
CalendarDate = Annotated[date, Field(ge=date(2000, 1, 1), le=date(2100, 12, 31))]
MonthParam = Annotated[str | None, Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
AppSettings = Annotated[Settings, Depends(get_settings)]


def _green_on(settings: AppSettings) -> None:
    green.require_on(settings)


GreenOn = Annotated[None, Depends(_green_on)]  # first, so off is a 404 before anything else


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


async def _household(session: DbSession, permit: Permit) -> identity_api.HouseholdRef:
    household = await identity_api.household_for_user(session, permit.principal.user_id)
    if household is None:
        raise service.not_found("subscriptions.not_found", "Onboard a household first.")
    return household


def _plan_out(plan: Plan, lang: Language) -> PlanOut:
    return PlanOut(
        code=plan.code,  # pyright: ignore[reportArgumentType]
        name=localised(plan.names, lang),
        fee_paise=plan.fee_paise,
        features=plan.features,
    )  # pyright: ignore[reportArgumentType]


async def _subscription_out(session: DbSession, s: Subscription) -> SubscriptionOut:
    plan = await session.get(Plan, s.plan_id)
    return SubscriptionOut(
        id=s.id,
        household_id=s.household_id,
        plan=plan.code if plan else "autopilot",  # pyright: ignore[reportArgumentType]
        status=s.status,  # pyright: ignore[reportArgumentType]
        payment_mode=s.payment_mode,  # pyright: ignore[reportArgumentType]
        paused_until=s.paused_until,
        auto_approve=s.auto_approve,
        started_at=s.started_at,
    )


async def _slot(session: DbSession, slot_id: uuid.UUID) -> SlotRefOut:
    slot = await orders_api.slot_ref(session, slot_id)
    if slot is None:
        raise ProblemError(500, "subscriptions.slot_missing", "The delivery slot no longer exists.")
    return SlotRefOut(id=slot.id, label=slot.label, start=slot.start, end=slot.end)


async def _captain(session: DbSession, society_id: uuid.UUID) -> CaptainOut | None:
    card = await community_api.captain_card(session, society_id)
    return (
        None
        if card is None
        else CaptainOut(display_name=card.display_name, photo_url=card.photo_url)
    )


async def _budget_out(session: DbSession, household_id: uuid.UUID, on) -> BudgetLockOut | None:
    budget = await service.budget_lock_for(session, household_id, on)
    if budget is None:
        return None
    spent = await service.spent_in_month(session, household_id, on)
    return BudgetLockOut(
        month=on.strftime("%Y-%m"),
        budget_paise=budget.budget_paise,
        spent_paise=spent,
        remaining_paise=max(0, budget.budget_paise - spent),
    )


async def _cycle_out(
    session: DbSession,
    cycle: WeeklyCycle | None,
    lines: list[BoxLine] | list[planning.DraftLine],
    *,
    household: identity_api.HouseholdRef,
    lang: Language,
    preview: planning.Draft | None = None,
    delivery_date=None,
    slot_id: uuid.UUID | None = None,
    plan_source: str = "fallback",
) -> CycleOut:
    product_ids: list[uuid.UUID] = list({line.product_id for line in lines})
    for line in lines:
        replaced_id = getattr(line, "replaces_product_id", None)
        if replaced_id is not None:
            product_ids.append(replaced_id)
    products = await catalog_api.line_products(session, product_ids, hub_id=household.hub_id)
    recipe_names = {r.id: r for r in (await catalog_api.plan_catalog(session, None)).recipes}
    out_lines = []
    for line in lines:
        product = products.get(line.product_id)
        replaced_id = getattr(line, "replaces_product_id", None)
        replaced = products.get(replaced_id) if replaced_id else None
        out_lines.append(
            CycleLineOut(
                product_id=line.product_id,
                slug=product.slug if product else "",
                name=localised(product.names, lang) if product else "",
                qty=line.qty,
                step=CycleStep(
                    qty=line.step_qty,
                    unit=line.step_unit,  # pyright: ignore[reportArgumentType]
                    label=catalog_api.step_label(line.step_qty, line.step_unit, lang),
                ),
                unit_price_paise=line.unit_price_paise,
                total_paise=line.qty * line.unit_price_paise,
                reason_code=line.reason_code,
                reason_params=line.reason_params,
                source=line.source,  # pyright: ignore[reportArgumentType]
                replaces=None
                if replaced is None
                else ReplacedOut(
                    product_id=replaced.id,
                    slug=replaced.slug,
                    name=localised(replaced.names, lang),
                    unit_price_paise=replaced.price_paise or 0,
                ),
            )
        )
    meal_days = cycle.meal_plan if cycle is not None else (preview.meal_plan if preview else [])
    days = []
    for day in meal_days:
        meals = []
        for meal in day["meals"]:
            recipe_id = uuid.UUID(meal["recipe_id"]) if meal.get("recipe_id") else None
            recipe = recipe_names.get(recipe_id) if recipe_id else None
            meals.append(
                MealOut(
                    slot=meal["slot"],
                    recipe_id=recipe_id,
                    name=localised(recipe.names, lang) if recipe else None,
                    tiffin=bool(meal.get("tiffin")),
                    sides=list(meal.get("sides", [])),
                    source=meal.get("source", "plan"),
                )
            )
        days.append(DayOut(date=day["date"], meals=meals))
    highlights = cycle.highlights if cycle is not None else (preview.highlights if preview else [])
    on = cycle.delivery_date if cycle is not None else delivery_date
    assert on is not None
    budget_out = await _budget_out(session, household.id, on)
    total = sum(line.qty * line.unit_price_paise for line in lines)
    due = await service.payment_due(session, cycle) if cycle is not None else None
    frozen = None
    if cycle is not None and cycle.order_id is not None:
        frozen = await orders_api.fees_of(session, cycle.order_id)  # set at approval (M4)
    if frozen is not None:
        fees = frozen
    elif household.hub_id is not None:
        fees = await orders_api.box_fee(session, household.hub_id, household.society_id)
    else:
        fees = 0
    discount = min(cycle.slot_saving_paise, total) if cycle is not None else 0
    moved = await green.green_slot(session, cycle) if cycle is not None else None
    return CycleOut(
        id=cycle.id if cycle else None,
        household_id=household.id,
        week_start=cycle.week_start if cycle else service.monday_of(on),
        delivery_date=on,
        slot=await _slot(session, cycle.slot_id if cycle else slot_id),  # pyright: ignore[reportArgumentType]
        cutoff_at=cycle.cutoff_at if cycle else orders_api.cutoff_for(on),
        status=cycle.status if cycle else "preview",  # pyright: ignore[reportArgumentType]
        version=cycle.version if cycle else 0,
        meal_plan=MealPlanOut(source=cycle.plan_source if cycle else plan_source, days=days),  # pyright: ignore[reportArgumentType]
        lines=out_lines,
        total_paise=total,
        highlights=[HighlightOut(code=h["code"], params=h.get("params", {})) for h in highlights],
        budget_lock=budget_out,
        captain=await _captain(session, household.society_id),
        order_id=cycle.order_id if cycle else None,
        generated_at=cycle.generated_at if cycle else None,
        approved_at=cycle.approved_at if cycle else None,
        payment_due=None
        if due is None
        else PaymentDueOut(
            order_id=due.order_id,
            total_paise=due.total_paise,
            wallet_paise=due.wallet_paise,
            shortfall_paise=due.shortfall_paise,
            due_at=due.due_at,
        ),
        fees_paise=fees,
        discount_paise=discount,
        payable_paise=max(0, total + fees - discount),
        green_slot=None if moved is None else GreenSlotOut.model_validate(moved),
    )


# --- plans and subscription ---------------------------------------------------------------------


@router.get("/plans", summary="The plan catalogue")
async def list_plans(_permit: CanRead, session: DbSession, lang: Lang) -> PlansOut:
    return PlansOut(items=[_plan_out(p, lang) for p in await service.list_plans(session)])


@router.post(
    "/subscriptions",
    status_code=status.HTTP_201_CREATED,
    summary="Subscribe the caller's household (one subscription per household)",
    responses={
        404: _problem("subscriptions.not_found: no household yet"),
        409: _problem("subscriptions.exists"),
        422: _problem("subscriptions.payment_mode_unavailable, subscriptions.plan_unknown"),
    },
)
async def subscribe(
    body: SubscriptionIn, permit: CanSubscribe, session: DbSession, idem: Idempotent
) -> SubscriptionOut:
    household = await _household(session, permit)
    subscription = await service.subscribe(
        session, household.id, plan_code=body.plan, payment_mode=body.payment_mode
    )
    out = await _subscription_out(session, subscription)
    await idem.save(201, out)
    await session.commit()
    return out


@router.get(
    "/subscriptions/current",
    summary="The caller's subscription",
    responses={404: _problem("subscriptions.not_found")},
)
async def get_subscription(permit: CanRead, session: DbSession) -> SubscriptionOut:
    household = await _household(session, permit)
    subscription = await service.subscription_of(session, household.id)
    if subscription is None:
        raise service.not_found()
    return await _subscription_out(session, subscription)


@router.patch(
    "/subscriptions/current",
    summary="Pause or resume, or set auto-approve",
    responses={404: _problem("subscriptions.not_found")},
)
async def patch_subscription(
    body: SubscriptionPatch, request: Request, permit: CanSubscribe, session: DbSession
) -> SubscriptionOut:
    household = await _household(session, permit)
    subscription = await service.subscription_of(session, household.id)
    if subscription is None:
        raise service.not_found()
    await service.update_subscription(
        session,
        subscription,
        set_pause="paused_until" in body.model_fields_set,
        paused_until=body.paused_until,
        auto_approve=body.auto_approve,
    )
    out = await _subscription_out(session, subscription)
    await session.commit()
    return out


# --- delivery days ------------------------------------------------------------------------------


@router.get("/societies/{society_id}/delivery-days", summary="A society's delivery days and slots")
async def get_delivery_days(
    society_id: uuid.UUID, _permit: CanRead, session: DbSession
) -> DeliveryDaysOut:
    days = await service.delivery_days(session, society_id)
    return DeliveryDaysOut(
        items=[
            DeliveryDayOut(weekday=d.weekday, slot=await _slot(session, d.slot_id)) for d in days
        ]
    )


@router.put(
    "/admin/societies/{society_id}/delivery-days",
    summary="Set a society's delivery days (hub manager of its hub, or admin; audit-logged)",
    responses={
        403: _problem("auth.forbidden"),
        422: _problem("subscriptions.delivery_days_invalid, subscriptions.society_unknown"),
    },
)
async def put_delivery_days(
    society_id: uuid.UUID,
    body: DeliveryDaysIn,
    permit: CanSetDays,
    session: DbSession,
    idem: Idempotent,
) -> DeliveryDaysOut:
    society = await community_api.get_society(session, society_id)
    if society is None or society.hub_id is None:
        raise ProblemError(
            422, "subscriptions.society_unknown", "No such society, or it has no hub yet."
        )
    permit.ensure(society.hub_id)
    days = await service.set_delivery_days(
        session, society_id, society.hub_id, [(i.weekday, i.slot_id) for i in body.items]
    )
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="delivery_days.set",
        entity_type="society",
        entity_id=str(society_id),
        after=body.model_dump(mode="json"),
    )
    out = DeliveryDaysOut(
        items=[
            DeliveryDayOut(weekday=d.weekday, slot=await _slot(session, d.slot_id)) for d in days
        ]
    )
    await idem.save(200, out)
    await session.commit()
    return out


@router.get(
    "/hubs/{hub_id}/delivery-dates",
    summary="The hub's next delivery dates, with the cut-off of each (hub staff, admin)",
    responses={403: _problem("auth.forbidden")},
)
async def hub_delivery_dates(
    hub_id: uuid.UUID,
    permit: CanSeeDates,
    session: DbSession,
    response: Response,
    from_: Annotated[
        CalendarDate | None,
        Query(
            alias="from",
            description="An IST date; default today",
        ),
    ] = None,
    days: Annotated[int, Query(ge=1, le=31, description="Calendar days from `from`")] = 14,
) -> HubDeliveryDatesOut:
    permit.ensure(hub_id)
    response.headers["Cache-Control"] = "no-store"
    return await cutoff.delivery_dates(session, hub_id, from_, days)


@router.get(
    "/hubs/{hub_id}/cutoff",
    summary="The hub's cut-off monitor for a delivery date (hub manager, franchise owner, admin)",
    responses={
        403: _problem("auth.forbidden"),
        404: _problem("subscriptions.not_delivery_date"),
    },
)
async def hub_cutoff(
    hub_id: uuid.UUID,
    permit: CanRead,
    session: DbSession,
    response: Response,
    on: Annotated[
        CalendarDate | None,
        Query(
            alias="date",
            description="A delivery date; default the hub's first delivery date after today (IST)",
        ),
    ] = None,
) -> HubCutoffOut:
    permit.ensure(hub_id)
    response.headers["Cache-Control"] = "no-store"
    return await cutoff.monitor(session, hub_id, on)


# --- cycles -------------------------------------------------------------------------------------


@router.get(
    "/cycles/current",
    summary="This week's cycle: meal plan, lines with reasons, Budget Lock (ETag = version)",
    responses={404: _problem("subscriptions.no_cycle")},
)
async def current_cycle(
    permit: CanRead, session: DbSession, lang: Lang, response: Response
) -> CycleOut:
    household = await _household(session, permit)
    cycle = await service.current_cycle(session, household.id, IST_DATE())
    if cycle is None:
        raise service.not_found("subscriptions.no_cycle", "No week has been planned yet.")
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, cycle.version)
    return await _cycle_out(
        session, cycle, await service.lines_of(session, cycle.id), household=household, lang=lang
    )


@router.get(
    "/cycles/preview",
    summary="The first week, from today's prices and the household's rules; nothing is saved",
    responses={
        404: _problem("subscriptions.not_found"),
        422: _problem("subscriptions.no_delivery_day"),
    },
)
async def preview_cycle(
    request: Request,
    permit: CanRead,
    session: DbSession,
    lang: Lang,
    response: Response,
    settings: AppSettings,
) -> CycleOut:
    household = await _household(session, permit)
    profile = await identity_api.household_plan_profile(session, household.id)
    assert profile is not None
    days = await service.delivery_days(session, household.society_id)
    day = service.next_delivery(days, IST_DATE() + timedelta(days=2))
    if day is None:
        raise ProblemError(
            422, "subscriptions.no_delivery_day", "Your society has no delivery day yet."
        )
    delivery_date = IST_DATE() + timedelta(
        days=((day.weekday - (IST_DATE() + timedelta(days=2)).weekday()) % 7 or 7) + 2
    )
    catalog = await catalog_api.plan_catalog(session, household.hub_id)
    async with service.http_client(getattr(request.app.state, "http", None)) as http:
        # No ranker and the rule table for daily use: one ML call at most while the customer waits.
        draft, source, _ = await service.draft(
            session,
            http,
            settings,
            profile,
            catalog,
            delivery_date,
            record_for=None,
            preview=True,
        )
    response.headers["Cache-Control"] = "no-store"
    return await _cycle_out(
        session,
        None,
        draft.lines,
        household=household,
        lang=lang,
        preview=draft,
        delivery_date=delivery_date,
        slot_id=day.slot_id,
        plan_source=source,
    )


@router.get(
    "/cycles/{cycle_id}",
    summary="One week (history)",
    responses={404: _problem("subscriptions.no_cycle")},
)
async def get_cycle(
    cycle_id: uuid.UUID, permit: CanRead, session: DbSession, lang: Lang, response: Response
) -> CycleOut:
    household = await _household(session, permit)
    cycle = await service.get_cycle(session, household.id, cycle_id)
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, cycle.version)
    return await _cycle_out(
        session, cycle, await service.lines_of(session, cycle.id), household=household, lang=lang
    )


async def _owned_cycle(
    session: DbSession, permit: Permit, cycle_id: uuid.UUID
) -> tuple[identity_api.HouseholdRef, WeeklyCycle]:
    household = await _household(session, permit)
    cycle = await service.get_cycle(session, household.id, cycle_id)
    return household, cycle


async def _answer(
    session: DbSession,
    cycle: WeeklyCycle,
    *,
    household: identity_api.HouseholdRef,
    lang: Language,
    response: Response,
) -> CycleOut:
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, cycle.version)
    return await _cycle_out(
        session, cycle, await service.lines_of(session, cycle.id), household=household, lang=lang
    )


@router.patch(
    "/cycles/{cycle_id}/lines",
    summary="Remove, add, change or substitute lines before the cut-off (If-Match)",
    responses={
        404: _problem("subscriptions.no_cycle"),
        409: _problem("subscriptions.cutoff_passed, subscriptions.not_editable"),
        412: _problem("subscriptions.version_conflict"),
        422: _problem(
            "subscriptions.product_unknown, subscriptions.substitute_unknown, "
            "subscriptions.qty_invalid, subscriptions.budget_exceeded (over_paise)"
        ),
        428: _problem("request.precondition_required"),
    },
)
async def patch_lines(
    cycle_id: uuid.UUID,
    body: LinesPatch,
    permit: CanWrite,
    session: DbSession,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> CycleOut:
    household, cycle = await _owned_cycle(session, permit, cycle_id)
    ops = [
        service.Op(
            op.op,
            op.product_id,
            qty=getattr(op, "qty", None),
            substitute_id=getattr(op, "substitute_id", None),
        )
        for op in body.ops
    ]
    await service.edit_lines(session, cycle, ops, expected_version=expected)
    out = await _answer(session, cycle, household=household, lang=lang, response=response)
    await session.commit()
    return out


@router.put(
    "/cycles/{cycle_id}/meals",
    summary="Set one meal (next dish, a recipe, tiffin, no-cook, or the plan's) and redraw its "
    "lines before the cut-off (If-Match)",
    responses={
        404: _problem("subscriptions.no_cycle"),
        409: _problem("subscriptions.cutoff_passed, subscriptions.not_editable"),
        412: _problem("subscriptions.version_conflict"),
        422: _problem(
            "subscriptions.meal_unknown, subscriptions.recipe_unknown, "
            "subscriptions.recipe_not_allowed (rules), subscriptions.no_alternative, "
            "subscriptions.budget_exceeded (over_paise)"
        ),
        428: _problem("request.precondition_required"),
    },
)
async def set_meal(
    cycle_id: uuid.UUID,
    body: MealSet,
    permit: CanWrite,
    session: DbSession,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> CycleOut:
    household, cycle = await _owned_cycle(session, permit, cycle_id)
    await service.set_meal(
        session,
        cycle,
        on=body.date,
        slot=body.slot,
        choice=body.set,
        recipe_id=body.recipe_id,
        expected_version=expected,
    )
    out = await _answer(session, cycle, household=household, lang=lang, response=response)
    await session.commit()
    return out


async def _quote_out(session: DbSession, quote: SlotQuote, cycle: WeeklyCycle) -> SlotQuoteOut:
    """The quote with each slot's room as it is now; route costs stay in the row."""
    live = {s.id: s for s in await orders_api.box_slots(session, cycle.hub_id, quote.delivery_date)}
    items = []
    for item in quote.items:
        slot_id = uuid.UUID(item["slot_id"])
        now = live.get(slot_id)
        items.append(
            SlotOptionOut(
                slot=await _slot(session, slot_id)
                if now is None
                else SlotRefOut(id=now.id, label=now.label, start=now.start, end=now.end),
                current=slot_id == cycle.slot_id,
                available=0 if now is None else now.available,
                green=item["green"],
                saving_paise=item["saving_paise"],
                rank=item["rank"],
                reason_code=item["reason_code"],
                reason_params=item["reason_params"],
            )
        )
    return SlotQuoteOut(
        id=quote.id,
        cycle_id=quote.cycle_id,
        delivery_date=quote.delivery_date,
        expires_at=quote.expires_at,
        source=quote.source,  # pyright: ignore[reportArgumentType]
        items=items,
    )


@router.post(
    "/cycles/{cycle_id}/slot-quotes",
    status_code=status.HTTP_201_CREATED,
    summary="Green-slot quote: every box slot that date with its saving, valid 15 minutes",
    description="201 with a new quote (one ML call); 200 with the cycle's quote that has not "
    "expired. Moves no money, so no Idempotency-Key (M3 §3.6, M10 §3.8).",
    responses={
        200: {"model": SlotQuoteOut, "description": "The quote that has not expired yet"},
        404: _problem("subscriptions.no_cycle, subscriptions.green_slots_off"),
        409: _problem("subscriptions.cutoff_passed, subscriptions.not_editable"),
    },
)
async def quote_slots(
    _on: GreenOn,
    cycle_id: uuid.UUID,
    request: Request,
    permit: CanWrite,
    session: DbSession,
    response: Response,
    settings: AppSettings,
) -> SlotQuoteOut:
    _, cycle = await _owned_cycle(session, permit, cycle_id)
    lines = await service.lines_of(session, cycle.id)
    async with service.http_client(getattr(request.app.state, "http", None)) as http:
        quote, made = await green.quote(
            session,
            http,
            settings,
            cycle,
            total_paise=sum(line.qty * line.unit_price_paise for line in lines),
        )
    if not made:
        response.status_code = status.HTTP_200_OK
    response.headers["Cache-Control"] = "no-store"
    out = await _quote_out(session, quote, cycle)
    await session.commit()
    return out


@router.put(
    "/cycles/{cycle_id}/slot",
    summary="Move this week's box to a quoted slot on the same date, at the quote's saving "
    "(If-Match)",
    responses={
        404: _problem("subscriptions.no_cycle, subscriptions.green_slots_off"),
        409: _problem(
            "subscriptions.cutoff_passed, subscriptions.not_editable, "
            "subscriptions.quote_expired, orders.slot_full"
        ),
        412: _problem("subscriptions.version_conflict"),
        422: _problem("subscriptions.quote_invalid: not this week's quote, or not its slot"),
        428: _problem("request.precondition_required"),
    },
)
async def move_slot(
    _on: GreenOn,
    cycle_id: uuid.UUID,
    body: SlotMoveIn,
    permit: CanWrite,
    session: DbSession,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> CycleOut:
    household, cycle = await _owned_cycle(session, permit, cycle_id)
    await green.move(
        session, cycle, slot_id=body.slot_id, quote_id=body.quote_id, expected_version=expected
    )
    out = await _answer(session, cycle, household=household, lang=lang, response=response)
    await session.commit()
    return out


@router.post(
    "/cycles/{cycle_id}/approve",
    summary="Approve the week: an order with these lines and prices (Idempotency-Key, If-Match)",
    responses={
        404: _problem("subscriptions.no_cycle"),
        409: _problem("subscriptions.cutoff_passed, subscriptions.not_editable, orders.slot_full"),
        412: _problem("subscriptions.version_conflict"),
        422: _problem("subscriptions.qty_invalid: the box is empty"),
        428: _problem("request.precondition_required"),
    },
)
async def approve_cycle(
    cycle_id: uuid.UUID,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> CycleOut:
    household, cycle = await _owned_cycle(session, permit, cycle_id)
    await service.approve(session, cycle, expected_version=expected)
    out = await _answer(session, cycle, household=household, lang=lang, response=response)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/cycles/{cycle_id}/skip",
    summary="Skip the week before the cut-off (a payment_failed week until its grace ends); its "
    "order is cancelled (Idempotency-Key)",
    responses={
        404: _problem("subscriptions.no_cycle"),
        409: _problem("subscriptions.cutoff_passed, subscriptions.not_editable"),
    },
)
async def skip_cycle(
    cycle_id: uuid.UUID,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    response: Response,
) -> CycleOut:
    household, cycle = await _owned_cycle(session, permit, cycle_id)
    await service.skip(session, cycle)
    out = await _answer(session, cycle, household=household, lang=lang, response=response)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/cycles/{cycle_id}/unskip",
    summary="Bring a skipped week back to approval before the cut-off (Idempotency-Key, If-Match)",
    description="The week returns with the lines it had (awaiting_approval, or draft if the "
    "box-ready notice never went out); approving it again creates a new order.",
    responses={
        404: _problem("subscriptions.no_cycle"),
        409: _problem(
            "subscriptions.not_skipped, subscriptions.cutoff_passed, subscriptions.paused "
            "(resume the subscription first)"
        ),
        412: _problem("subscriptions.version_conflict"),
        428: _problem("request.precondition_required"),
    },
)
async def unskip_cycle(
    cycle_id: uuid.UUID,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> CycleOut:
    household, cycle = await _owned_cycle(session, permit, cycle_id)
    await service.unskip(session, cycle, expected_version=expected)
    out = await _answer(session, cycle, household=household, lang=lang, response=response)
    await idem.save(200, out)
    await session.commit()
    return out


@router.post(
    "/cycles/{cycle_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Ran out, wasted or just right per item, 14 days from delivery (Idempotency-Key)",
    responses={
        404: _problem("subscriptions.no_cycle"),
        422: _problem("subscriptions.feedback_window_closed, subscriptions.product_unknown"),
    },
)
async def give_feedback(
    cycle_id: uuid.UUID,
    body: FeedbackIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
) -> FeedbackOut:
    _, cycle = await _owned_cycle(session, permit, cycle_id)
    recorded = await service.record_feedback(
        session, cycle, {item.product_id: item.signal for item in body.items}, today=IST_DATE()
    )
    out = FeedbackOut(recorded=recorded)
    await idem.save(201, out)
    await session.commit()
    return out


# --- Budget Lock --------------------------------------------------------------------------------


def _month(value: str | None) -> datetime:
    if value is None:
        return datetime.combine(IST_DATE().replace(day=1), datetime.min.time())
    return datetime.strptime(value, "%Y-%m")


@router.get(
    "/budget-lock",
    summary="The month's Budget Lock with what the approved boxes spent",
    responses={404: _problem("subscriptions.no_budget_lock")},
)
async def get_budget_lock(
    permit: CanRead, session: DbSession, month: MonthParam = None
) -> BudgetLockOut:
    household = await _household(session, permit)
    out = await _budget_out(session, household.id, _month(month).date())
    if out is None:
        raise service.not_found("subscriptions.no_budget_lock", "No Budget Lock for that month.")
    return out


@router.put(
    "/budget-lock",
    summary="Set the month's Budget Lock (≥ ₹500, this month or next); null switches it off (204)",
    responses={
        204: {"description": "Switched off"},
        404: _problem("subscriptions.not_found"),
        422: _problem("subscriptions.budget_invalid"),
    },
)
async def put_budget_lock(
    body: BudgetLockIn, permit: CanWrite, session: DbSession, idem: Idempotent
) -> BudgetLockOut:
    household = await _household(session, permit)
    month = _month(body.month).date()
    lock = await service.set_budget_lock(session, household.id, month, body.budget_paise)
    if lock is None:
        await idem.save(204, None)
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)  # pyright: ignore[reportReturnType]
    out = await _budget_out(session, household.id, month)
    assert out is not None
    await idem.save(200, out)
    await session.commit()
    return out
