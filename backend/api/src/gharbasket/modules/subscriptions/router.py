"""HTTP endpoints of M3 Plans & Subscriptions (spec §3.1): plans, the subscription, delivery days
and reading cycles. Edits, approve, skip, feedback and the Budget Lock follow in part 2."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import jobs as _jobs  # noqa: F401 (registers the job)
from gharbasket.modules.subscriptions import permissions as _permissions  # noqa: F401
from gharbasket.modules.subscriptions import planning, service
from gharbasket.modules.subscriptions.models import BoxLine, Plan, Subscription, WeeklyCycle
from gharbasket.modules.subscriptions.schemas import (
    BudgetLockOut,
    CycleLineOut,
    CycleOut,
    CycleStep,
    DayOut,
    DeliveryDayOut,
    DeliveryDaysIn,
    DeliveryDaysOut,
    HighlightOut,
    MealOut,
    MealPlanOut,
    PlanOut,
    PlansOut,
    ReplacedOut,
    SlotRefOut,
    SubscriptionIn,
    SubscriptionOut,
    SubscriptionPatch,
)
from gharbasket.modules.subscriptions.service import IST_DATE
from gharbasket.platform import audit
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["subscriptions"])

CanRead = Annotated[Permit, require("cycles:read")]
CanSubscribe = Annotated[Permit, require("subscriptions:write")]
CanSetDays = Annotated[Permit, require("delivery_days:write")]


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
    products = await catalog_api.line_products(session, product_ids)
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
                    unit_price_paise=0,
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
                )
            )
        days.append(DayOut(date=day["date"], meals=meals))
    highlights = cycle.highlights if cycle is not None else (preview.highlights if preview else [])
    on = cycle.delivery_date if cycle is not None else delivery_date
    assert on is not None
    budget = await service.budget_lock_for(session, household.id, on)
    budget_out = None
    if budget is not None:
        spent = await service.spent_in_month(session, household.id, on)
        budget_out = BudgetLockOut(
            month=on.strftime("%Y-%m"),
            budget_paise=budget.budget_paise,
            spent_paise=spent,
            remaining_paise=max(0, budget.budget_paise - spent),
        )
    total = sum(line.qty * line.unit_price_paise for line in lines)
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
        captain=None,
        order_id=cycle.order_id if cycle else None,
        generated_at=cycle.generated_at if cycle else None,
        approved_at=cycle.approved_at if cycle else None,
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
    settings: Annotated[Settings, Depends(get_settings)],
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
        draft, source, _ = await service.draft(
            session, http, settings, profile, catalog, delivery_date, record_for=None
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


def _now() -> datetime:
    return datetime.now(UTC)
