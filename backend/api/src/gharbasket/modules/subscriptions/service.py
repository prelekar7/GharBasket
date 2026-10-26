"""Business logic of M3 Plans & Subscriptions (spec §3.1): plans, the subscription, delivery
days, generating a week's cycle, reading, editing, approving, skipping and un-skipping it (§3.3),
the pantry feedback after delivery, and the Budget Lock.

Approval hands the box to M4 through `orders.api.create_from_cycle` in the same transaction,
so the answer carries `order_id` and a full slot is refused on the spot; the
`subscriptions.box_approved` event still goes out for M11 and analytics.
"""

import copy
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import structlog
from sqlalchemy import ColumnElement, and_, delete, func, not_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.intel_gateway.api import (
    PantryItem,
    PantryObservation,
    PantryRequest,
    PlanRequest,
)
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import events, planning, rules
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    BudgetLock,
    EditFeedback,
    PantryFeedback,
    Plan,
    SocietyDeliveryDay,
    Subscription,
    WeeklyCycle,
)
from gharbasket.modules.subscriptions.rules import Member
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.subscriptions")


@asynccontextmanager
async def http_client(given: httpx2.AsyncClient | None) -> AsyncIterator[httpx2.AsyncClient]:
    """The app's shared client, or a short-lived one (tests, tools) that is closed afterwards."""
    if given is not None:
        yield given
        return
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(5.0)) as client:
        yield client


MAX_DELIVERY_DAYS = 3
MAX_QTY = 20  # steps a line may hold
FEEDBACK_DAYS = 14  # the pantry taps stay open this long after delivery
MIN_BUDGET_PAISE = 50_000  # ₹500
EDITABLE = ("awaiting_approval", "approved")
IST = ZoneInfo("Asia/Kolkata")


def IST_DATE() -> date:
    return datetime.now(IST).date()


def now() -> datetime:
    return datetime.now(UTC)


def not_found(
    what: str = "subscriptions.not_found", detail: str = "Nothing here yet."
) -> ProblemError:
    return ProblemError(404, what, detail)


# --- plans and subscriptions --------------------------------------------------------------------


async def list_plans(session: AsyncSession) -> list[Plan]:
    return list(
        (await session.execute(select(Plan).where(Plan.active).order_by(Plan.code))).scalars()
    )


async def admin_plans(session: AsyncSession) -> list[tuple[Plan, int]]:
    """Every plan by code, live or not, with the subscriptions on it that are not ended."""
    counts = (
        select(Subscription.plan_id, func.count().label("n"))
        .where(Subscription.status != "ended")
        .group_by(Subscription.plan_id)
        .subquery()
    )
    rows = await session.execute(
        select(Plan, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.plan_id == Plan.id)
        .order_by(Plan.code)
    )
    return [(plan, int(n)) for plan, n in rows.all()]


async def hub_delivery_days(
    session: AsyncSession, society_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[SocietyDeliveryDay]]:
    """The delivery days of several societies at once, by weekday."""
    found: dict[uuid.UUID, list[SocietyDeliveryDay]] = {sid: [] for sid in society_ids}
    if society_ids:
        rows = await session.execute(
            select(SocietyDeliveryDay)
            .where(SocietyDeliveryDay.society_id.in_(society_ids))
            .order_by(SocietyDeliveryDay.society_id, SocietyDeliveryDay.weekday)
        )
        for day in rows.scalars():
            found[day.society_id].append(day)
    return found


async def subscription_of(session: AsyncSession, household_id: uuid.UUID) -> Subscription | None:
    return (
        await session.execute(select(Subscription).where(Subscription.household_id == household_id))
    ).scalar_one_or_none()


async def subscribe(
    session: AsyncSession, household_id: uuid.UUID, *, plan_code: str, payment_mode: str
) -> Subscription:
    if payment_mode != "wallet":
        raise ProblemError(
            422, "subscriptions.payment_mode_unavailable", "UPI Autopay arrives with the pilot."
        )
    plan = (
        await session.execute(select(Plan).where(Plan.code == plan_code, Plan.active))
    ).scalar_one_or_none()
    if plan is None:
        raise ProblemError(422, "subscriptions.plan_unknown", "No such plan.")
    subscription = Subscription(
        household_id=household_id, plan_id=plan.id, payment_mode=payment_mode
    )
    try:
        async with session.begin_nested():
            session.add(subscription)
            await session.flush()
    except IntegrityError:
        raise ProblemError(
            409, "subscriptions.exists", "This household already has a subscription."
        ) from None
    await session.refresh(subscription)
    return subscription


async def update_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    set_pause: bool = False,
    paused_until: date | None = None,
    auto_approve: bool | None = None,
) -> Subscription:
    """`set_pause` says whether `paused_until` was sent (None then means resume)."""
    if set_pause:
        subscription.paused_until = paused_until
        paused = paused_until is not None and paused_until > IST_DATE()
        subscription.status = "paused" if paused else "active"
        if paused:
            await publish(
                session,
                events.SUBSCRIPTION_PAUSED,
                {
                    "subscription_id": str(subscription.id),
                    "household_id": str(subscription.household_id),
                    "paused_until": str(paused_until),
                },
            )
    if auto_approve is not None:
        subscription.auto_approve = auto_approve
    await session.flush()
    return subscription


def is_paused_on(subscription: Subscription, on: date) -> bool:
    return subscription.paused_until is not None and on < subscription.paused_until


# --- delivery days ------------------------------------------------------------------------------


async def delivery_days(session: AsyncSession, society_id: uuid.UUID) -> list[SocietyDeliveryDay]:
    rows = await session.execute(
        select(SocietyDeliveryDay)
        .where(SocietyDeliveryDay.society_id == society_id)
        .order_by(SocietyDeliveryDay.weekday)
    )
    return list(rows.scalars())


async def set_delivery_days(
    session: AsyncSession,
    society_id: uuid.UUID,
    hub_id: uuid.UUID,
    items: list[tuple[int, uuid.UUID]],
) -> list[SocietyDeliveryDay]:
    if not 1 <= len(items) <= MAX_DELIVERY_DAYS or len({w for w, _ in items}) != len(items):
        raise ProblemError(
            422,
            "subscriptions.delivery_days_invalid",
            f"1 to {MAX_DELIVERY_DAYS} distinct weekdays.",
        )
    for _, slot_id in items:
        slot = await orders_api.slot_ref(session, slot_id)
        if slot is None or slot.hub_id != hub_id or not slot.active or slot.kind != "box":
            raise ProblemError(
                422,
                "subscriptions.delivery_days_invalid",
                "Each day needs an active box slot of this hub.",
            )
    for existing in await delivery_days(session, society_id):
        await session.delete(existing)
    await session.flush()
    rows = [SocietyDeliveryDay(society_id=society_id, weekday=w, slot_id=s) for w, s in items]
    session.add_all(rows)
    await session.flush()
    return sorted(rows, key=lambda r: r.weekday)


def next_delivery(days: list[SocietyDeliveryDay], after: date) -> SocietyDeliveryDay | None:
    """The first delivery day strictly after `after`, with its slot."""
    best: tuple[int, SocietyDeliveryDay] | None = None
    for day in days:
        ahead = (day.weekday - after.weekday()) % 7 or 7
        if best is None or ahead < best[0]:
            best = (ahead, day)
    return None if best is None else best[1]


# --- cycles -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Generated:
    cycle: WeeklyCycle
    lines: list[BoxLine]
    created: bool


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def cycle_for(
    session: AsyncSession, household_id: uuid.UUID, delivery_date: date
) -> WeeklyCycle | None:
    return (
        await session.execute(
            select(WeeklyCycle).where(
                WeeklyCycle.household_id == household_id, WeeklyCycle.delivery_date == delivery_date
            )
        )
    ).scalar_one_or_none()


async def draft(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
    delivery_date: date,
    *,
    record_for: uuid.UUID | None,
    preview: bool = False,
) -> tuple[planning.Draft, str, uuid.UUID | None]:
    """A box for the week of `delivery_date`: (draft, plan source, prediction id).
    `preview=True` (the customer-facing preview) skips the ranker (no learned liking) and keeps
    the rule table for daily use (no pantry twin), so the preview waits on at most one ML call
    (the planner's, bounded by `ml_timeout_s`)."""
    monday = monday_of(delivery_date)
    festivals = await catalog_api.festivals_between(session, monday, monday + timedelta(days=6))
    price_index = await intel_api.price_spikes(session, list(catalog.products), monday)
    preferences = (
        None
        if preview
        else await learned_preferences(
            session, http, settings, profile, catalog, delivery_date, record_for=record_for
        )
    )
    request = planning.plan_request(
        profile, catalog, monday, festivals, price_index, preferences=preferences
    )
    plan = await intel_api.plan_week(
        http,
        settings,
        PlanRequest.model_validate(request),
        session=session if record_for else None,
        household_id=record_for,
        week_monday=monday,
    )
    meals = [m.model_dump() for m in plan.plan] if plan.plan else None
    bought = await bought_recently(session, profile.id, delivery_date, catalog)
    quantities = (
        await twin_quantities(
            session, http, settings, profile, catalog, delivery_date, record_for=record_for
        )
        if not preview
        else {}
    )
    lines = planning.lines_from_plan(
        profile, catalog, meals, fallback=plan.plan is None, bought=bought, quantities=quantities
    )
    monthly_budget = profile.budget_paise
    budget_near = (
        monthly_budget is not None
        and sum(line.total_paise for line in lines) * 30 // 7 > monthly_budget
    )
    draft_box = planning.Draft(
        lines=lines,
        meal_plan=planning.meal_plan_days(meals, catalog, monday),
        highlights=planning.highlights_for(plan.source, request["fasting_days"], budget_near),
    )
    prediction = None
    if record_for:
        kept = await intel_api.latest_prediction(
            session, kind="weekly_plan", subject_id=record_for, for_date=monday
        )
        prediction = kept.id if kept else None
    return draft_box, plan.source, prediction


MIN_RANKED_WEEKS = 2  # cycles the household must have before its liking counts
WEEKS_SINCE_NEVER = 52.0  # the training job's cap on weeks since last bought (M10 §3.7)
RANK_TOP_K = 100  # the most products the ranker returns scores for
MAX_CANDIDATES = 500  # the most candidates one ranker call takes
FINAL = ("approved", "locked")  # a cycle with a final box (the ranker's baskets)


async def ranker_candidates(
    session: AsyncSession,
    household_id: uuid.UUID,
    hub_id: uuid.UUID,
    delivery_date: date,
    catalog: catalog_api.PlanCatalog,
) -> list[intel_api.RankCandidate]:
    """The ranker's candidates and features from the household's past boxes, with the training
    job's definitions (backend/training ranker.py, M10 §3.7): every product it ever kept in an
    approved or locked box; weeks since it was last kept (capped at 52) and the share of those
    boxes it was in, over the full history; removed last time (removed or swapped out of the
    previous final box and not in it); the share of the hub's households with a final box in the
    hub's latest earlier week that had it (popularity); and the hub's price valid at this
    cycle's cut-off. Empty until the household has MIN_RANKED_WEEKS past final boxes."""
    past = list(
        (
            await session.execute(
                select(WeeklyCycle)
                .where(
                    WeeklyCycle.household_id == household_id,
                    WeeklyCycle.delivery_date < delivery_date,
                    WeeklyCycle.status.in_(FINAL),
                )
                .order_by(WeeklyCycle.delivery_date)
            )
        ).scalars()
    )
    if len(past) < MIN_RANKED_WEEKS:
        return []
    by_cycle: dict[uuid.UUID, set[uuid.UUID]] = {c.id: set() for c in past}
    lines = await session.execute(
        select(BoxLine.cycle_id, BoxLine.product_id).where(BoxLine.cycle_id.in_(list(by_cycle)))
    )
    for cycle_id, product_id in lines.all():
        by_cycle[cycle_id].add(product_id)
    last = past[-1]
    in_last_basket = by_cycle[last.id]
    removed = {
        product_id
        for (product_id,) in (
            await session.execute(
                select(EditFeedback.product_id).where(
                    EditFeedback.cycle_id == last.id,
                    EditFeedback.action.in_(("removed", "swapped")),
                )
            )
        ).all()
    } - in_last_basket
    popularity = await _hub_popularity(session, hub_id, delivery_date)
    kept_weeks: dict[uuid.UUID, list[date]] = {}
    for cycle in past:
        for product_id in by_cycle[cycle.id]:
            kept_weeks.setdefault(product_id, []).append(cycle.delivery_date)
    cutoff_day = orders_api.cutoff_for(delivery_date).astimezone(IST).date()
    prices = await catalog_api.latest_prices_on(session, hub_id, list(kept_weeks), cutoff_day)
    candidates = []
    for product_id, weeks in sorted(kept_weeks.items(), key=lambda kv: str(kv[0])):
        product = catalog.products.get(product_id)
        if product is None:
            continue
        candidates.append(
            intel_api.RankCandidate(
                product=product.slug,
                weeks_since=min((delivery_date - weeks[-1]).days / 7, WEEKS_SINCE_NEVER),
                frequency=len(weeks) / len(past),
                removed_last=product_id in removed,
                popularity=round(min(1.0, popularity.get(product_id, 0.0)), 4),
                price_paise=prices.get(product_id, 0),
            )
        )
    if len(candidates) > MAX_CANDIDATES:  # the most often kept, most recently kept first
        candidates = sorted(candidates, key=lambda c: (-c.frequency, c.weeks_since, c.product))
        candidates = candidates[:MAX_CANDIDATES]
    return candidates


async def _hub_popularity(
    session: AsyncSession, hub_id: uuid.UUID, delivery_date: date
) -> dict[uuid.UUID, float]:
    """Per product, the share of the hub's households with a final box in the hub's latest week
    before `delivery_date`'s week that had it in that box."""
    latest = (
        await session.execute(
            select(func.max(WeeklyCycle.delivery_date)).where(
                WeeklyCycle.hub_id == hub_id,
                WeeklyCycle.delivery_date < monday_of(delivery_date),
                WeeklyCycle.status.in_(FINAL),
            )
        )
    ).scalar_one_or_none()
    if latest is None:
        return {}
    start = monday_of(latest)
    in_week = (
        WeeklyCycle.hub_id == hub_id,
        WeeklyCycle.delivery_date >= start,
        WeeklyCycle.delivery_date < start + timedelta(days=7),
        WeeklyCycle.status.in_(FINAL),
    )
    households = (
        await session.execute(
            select(func.count(func.distinct(WeeklyCycle.household_id))).where(*in_week)
        )
    ).scalar_one()
    counts = (
        await session.execute(
            select(BoxLine.product_id, func.count(func.distinct(WeeklyCycle.household_id)))
            .join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id)
            .where(*in_week)
            .group_by(BoxLine.product_id)
        )
    ).all()
    return {product_id: n / households for product_id, n in counts} if households else {}


async def learned_preferences(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
    delivery_date: date,
    *,
    record_for: uuid.UUID | None,
) -> dict[str, float] | None:
    """v2: the ranker's score per product the household has kept before, turned into a
    liking per dish for the planner (`planning.recipe_preferences`). None for new households.
    The ranker returns at most RANK_TOP_K scores; a candidate below them gets the lowest score
    returned (it ranked no higher), so a dish is not liked more for missing its weaker items."""
    if profile.hub_id is None:
        return None
    candidates = await ranker_candidates(
        session, profile.id, profile.hub_id, delivery_date, catalog
    )
    if not candidates:
        return None
    ranked = await intel_api.rank_basket(
        http,
        settings,
        intel_api.RankRequest(candidates=candidates, top_k=min(len(candidates), RANK_TOP_K)),
        session=session if record_for else None,
        household_id=record_for,
        week_monday=monday_of(delivery_date),
    )
    by_slug = {p.slug: p.id for p in catalog.products.values()}
    scores = {by_slug[r.product]: r.score for r in ranked.ranked if r.product in by_slug}
    if scores:
        floor = min(scores.values())
        for c in candidates:
            if c.product in by_slug:
                scores.setdefault(by_slug[c.product], floor)
    return planning.recipe_preferences(scores, catalog)


OBSERVED_WEEKS = 12  # how far back the twin looks at boxes and taps
RULE_PRIOR_WEEKS = 2.0  # the rule table's weight as the twin's prior


@dataclass(frozen=True)
class DailyUseShare:
    """One locked box's daily-use share of one twin product (M10 §3.3): what the box delivered
    minus what that week's planned recipes needed, never below 0, and the household's tap."""

    delivery_date: date
    data_source: str  # the cycle's: real | synthetic
    product_id: uuid.UUID
    share: float  # base units
    signal: str | None


async def daily_use_history(
    session: AsyncSession,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
    *,
    since: date | None = None,
    before: date | None = None,
) -> list[DailyUseShare]:
    """Every locked box of the household delivered in [since, before) that carried a twin
    product (perishable daily use), oldest first, as its daily-use share and tap. The share is
    computed the way the draft computes the need (`planning.recipe_need` on the cycle's stored
    `meal_plan`), so the twin learns daily use and never the recipes on top. The serving twin
    (`pantry_observations`) and the weekly refit of its cohort priors (`backend/training`,
    `refit_pantry`) both read this, so they observe the same thing."""
    query = select(WeeklyCycle).where(
        WeeklyCycle.household_id == profile.id, WeeklyCycle.status == "locked"
    )
    if since is not None:
        query = query.where(WeeklyCycle.delivery_date >= since)
    if before is not None:
        query = query.where(WeeklyCycle.delivery_date < before)
    cycles = list((await session.execute(query.order_by(WeeklyCycle.delivery_date))).scalars())
    products = [p for p in catalog.products.values() if planning.twin_product(p)]
    if not cycles or not products:
        return []
    ids = [c.id for c in cycles]
    wanted = [p.id for p in products]
    delivered: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    for line in (
        await session.execute(
            select(BoxLine).where(BoxLine.cycle_id.in_(ids), BoxLine.product_id.in_(wanted))
        )
    ).scalars():
        key = (line.cycle_id, line.product_id)
        delivered[key] = delivered.get(key, 0.0) + (
            line.qty * line.step_qty * planning.BASE_FACTOR[line.step_unit]
        )
    taps = {
        (cycle_id, product_id): signal
        for cycle_id, product_id, signal in await session.execute(
            select(PantryFeedback.cycle_id, PantryFeedback.product_id, PantryFeedback.signal).where(
                PantryFeedback.cycle_id.in_(ids), PantryFeedback.product_id.in_(wanted)
            )
        )
    }
    out: list[DailyUseShare] = []
    for cycle in cycles:
        need, _ = planning.recipe_need(profile, catalog, planning.plan_of(cycle.meal_plan))
        for product in products:
            units = delivered.get((cycle.id, product.id))
            if not units:
                continue  # not in that week's box: nothing observed
            out.append(
                DailyUseShare(
                    cycle.delivery_date,
                    cycle.data_source,
                    product.id,
                    max(0.0, units - need.get(product.id, 0.0)),
                    taps.get((cycle.id, product.id)),
                )
            )
    return out


async def pantry_observations(
    session: AsyncSession,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
    delivery_date: date,
) -> dict[uuid.UUID, list[tuple[float, str | None]]]:
    """Per twin product (perishable daily use): each locked box of the last OBSERVED_WEEKS weeks
    that carried it, oldest first, as (daily-use share in base units, the household's tap)."""
    out: dict[uuid.UUID, list[tuple[float, str | None]]] = {}
    for row in await daily_use_history(
        session,
        profile,
        catalog,
        since=delivery_date - timedelta(weeks=OBSERVED_WEEKS),
        before=delivery_date,
    ):
        out.setdefault(row.product_id, []).append((row.share, row.signal))
    return out


async def twin_quantities(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
    delivery_date: date,
    *,
    record_for: uuid.UUID | None,
) -> dict[uuid.UUID, tuple[float, dict[str, Any]]]:
    """The pantry twin's weekly daily use for the perishable daily-use items (M10 §3.3): the
    cohort prior is the promoted learned prior per adult equivalent times the household's
    equivalents (v1, `intel.pantry_priors`), else the rule table; the daily-use share of the
    household's locked boxes and its taps are the observations. Empty when the twin is switched
    off (`pantry_twin_enabled`) or the household has no observed week yet, so the rule table
    drafts those boxes."""
    if not settings.pantry_twin_enabled:
        return {}
    observed = await pantry_observations(session, profile, catalog, delivery_date)
    equivalents = rules.adult_equivalents([Member(m) for m in profile.member_age_bands])
    learned = await intel_api.pantry_priors(session, rules.cohort_of(equivalents))  # v1
    items = []
    prior_of: dict[str, str] = {}
    for slug, prior in rules.daily_use_plan(equivalents).items():
        product = catalog.product_by_slug(slug)
        if product is None or product.id not in observed:
            continue
        # v1: the learned prior is per adult equivalent, so it scales with the household as
        # the rule table does; without one (too few households, or before a promotion) the
        # table and its weight of two weeks.
        if product.id in learned:
            per_equivalent, prior_weeks = learned[product.id]
            prior_mean, prior_of[slug] = per_equivalent * equivalents, "learned"
        else:
            prior_mean, prior_weeks, prior_of[slug] = prior, RULE_PRIOR_WEEKS, "rule"
        items.append(
            PantryItem(
                product=slug,
                step=product.step_qty * planning.BASE_FACTOR[product.step_unit],
                prior_mean=prior_mean,
                prior_weeks=prior_weeks,
                observations=[
                    PantryObservation(qty=qty, signal=signal)  # pyright: ignore[reportArgumentType]
                    for qty, signal in observed[product.id]
                ],
            )
        )
    if not items:
        return {}
    answer = await intel_api.pantry_quantities(
        http,
        settings,
        PantryRequest(items=items),
        session=session if record_for else None,
        household_id=record_for,
        for_date=delivery_date,
    )
    by_slug = {item.product: item for item in answer.items}
    return {
        product.id: (
            by_slug[slug].mean,
            {
                "mean": by_slug[slug].mean,
                "p10": by_slug[slug].p10,
                "p90": by_slug[slug].p90,
                "weeks": by_slug[slug].weeks_observed,
                "source": answer.source,
                "prior": prior_of.get(slug, "rule"),
            },
        )
        for slug in by_slug
        if (product := catalog.product_by_slug(slug)) is not None
    }


async def bought_recently(
    session: AsyncSession,
    household_id: uuid.UUID,
    delivery_date: date,
    catalog: catalog_api.PlanCatalog,
) -> dict[uuid.UUID, list[tuple[float, float]]]:
    """Per dry staple, (weeks before `delivery_date`, base units) of each approved or locked box
    of the last CARRY_WEEKS weeks: the deliveries the next draft's cupboard is made of."""
    since = delivery_date - timedelta(weeks=planning.CARRY_WEEKS)
    rows = await session.execute(
        select(BoxLine, WeeklyCycle.delivery_date)
        .join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id)
        .where(
            WeeklyCycle.household_id == household_id,
            WeeklyCycle.delivery_date >= since,
            WeeklyCycle.delivery_date < delivery_date,
            WeeklyCycle.status.in_(("approved", "locked")),
        )
    )
    bought: dict[uuid.UUID, list[tuple[float, float]]] = {}
    for line, delivered in rows.all():
        product = catalog.products.get(line.product_id)
        if product is None or not planning.carries_over(product):
            continue
        bought.setdefault(line.product_id, []).append(
            (
                (delivery_date - delivered).days / 7,
                line.qty * line.step_qty * planning.BASE_FACTOR[line.step_unit],
            )
        )
    return bought


async def generate_cycle(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    *,
    household_id: uuid.UUID,
    delivery_date: date,
    catalog: catalog_api.PlanCatalog,
) -> Generated | None:
    """The household's cycle for `delivery_date`, created once as a `draft`; None when nothing
    applies (no subscription, paused, no delivery day for the society, no household). The
    customer hears of it, and may edit it, once `send_box_ready_notices` has run."""
    existing = await cycle_for(session, household_id, delivery_date)
    if existing is not None:
        lines = list(
            (
                await session.execute(select(BoxLine).where(BoxLine.cycle_id == existing.id))
            ).scalars()
        )
        return Generated(existing, lines, False)
    profile = await identity_api.household_plan_profile(session, household_id)
    if profile is None or profile.hub_id is None or not profile.autopilot_enabled:
        return None
    subscription = await subscription_of(session, household_id)
    if (
        subscription is None
        or subscription.status == "ended"
        or is_paused_on(subscription, delivery_date)
    ):
        return None
    day = next(
        (
            d
            for d in await delivery_days(session, profile.society_id)
            if d.weekday == delivery_date.weekday()
        ),
        None,
    )
    if day is None:
        return None
    box, source, prediction = await draft(
        session, http, settings, profile, catalog, delivery_date, record_for=household_id
    )
    cycle = WeeklyCycle(
        household_id=household_id,
        hub_id=profile.hub_id,
        society_id=profile.society_id,
        subscription_id=subscription.id,
        week_start=monday_of(delivery_date),
        delivery_date=delivery_date,
        slot_id=day.slot_id,
        cutoff_at=orders_api.cutoff_for(delivery_date),
        plan_prediction_id=prediction,
        plan_source=source,
        meal_plan=box.meal_plan,
        highlights=box.highlights,
        status="draft",
        generated_at=now(),
        data_source=profile.data_source,
    )
    try:
        async with session.begin_nested():
            session.add(cycle)
            await session.flush()
    except IntegrityError:  # generated twice at the same moment
        found = await cycle_for(session, household_id, delivery_date)
        if found is None:
            raise
        lines = list(
            (await session.execute(select(BoxLine).where(BoxLine.cycle_id == found.id))).scalars()
        )
        return Generated(found, lines, False)
    lines = [
        BoxLine(
            cycle_id=cycle.id,
            product_id=line.product_id,
            qty=line.qty,
            step_qty=line.step_qty,
            step_unit=line.step_unit,
            unit_price_paise=line.unit_price_paise,
            reason_code=line.reason_code,
            reason_params=line.reason_params,
            source=line.source,
        )
        for line in box.lines
    ]
    session.add_all(lines)
    await session.flush()
    await publish(
        session,
        events.CYCLE_GENERATED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(household_id),
            "hub_id": str(profile.hub_id),
            "delivery_date": delivery_date.isoformat(),
            "total_paise": box.total_paise,
            "source": source,
        },
    )
    await session.refresh(cycle)
    return Generated(cycle, lines, True)


async def generate_for_hub(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    *,
    hub_id: uuid.UUID,
    delivery_date: date,
) -> dict[str, int]:
    """The job's body for one hub: a cycle per Autopilot household due that day."""
    catalog = await catalog_api.plan_catalog(session, hub_id)
    counts = {"created": 0, "existing": 0, "skipped": 0}
    for household_id in await identity_api.households_of_hub(session, hub_id):
        result = await generate_cycle(
            session,
            http,
            settings,
            household_id=household_id,
            delivery_date=delivery_date,
            catalog=catalog,
        )
        if result is None:
            counts["skipped"] += 1
        elif result.created:
            counts["created"] += 1
        else:
            counts["existing"] += 1
    return counts


async def current_cycle(
    session: AsyncSession, household_id: uuid.UUID, today: date
) -> WeeklyCycle | None:
    """The cycle whose delivery is next (today or later), else the latest one."""
    upcoming = (
        await session.execute(
            select(WeeklyCycle)
            .where(WeeklyCycle.household_id == household_id, WeeklyCycle.delivery_date >= today)
            .order_by(WeeklyCycle.delivery_date)
            .limit(1)
        )
    ).scalar_one_or_none()
    if upcoming is not None:
        return upcoming
    return (
        await session.execute(
            select(WeeklyCycle)
            .where(WeeklyCycle.household_id == household_id)
            .order_by(WeeklyCycle.delivery_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_cycle(
    session: AsyncSession, household_id: uuid.UUID, cycle_id: uuid.UUID
) -> WeeklyCycle:
    cycle = await session.get(WeeklyCycle, cycle_id)
    if cycle is None or cycle.household_id != household_id:
        raise not_found("subscriptions.no_cycle", "No such week.")
    return cycle


async def lines_of(session: AsyncSession, cycle_id: uuid.UUID) -> list[BoxLine]:
    rows = await session.execute(
        select(BoxLine).where(BoxLine.cycle_id == cycle_id).order_by(BoxLine.product_id)
    )
    return list(rows.scalars())


async def budget_lock_for(
    session: AsyncSession, household_id: uuid.UUID, month: date
) -> BudgetLock | None:
    return (
        await session.execute(
            select(BudgetLock).where(
                BudgetLock.household_id == household_id, BudgetLock.month == month.replace(day=1)
            )
        )
    ).scalar_one_or_none()


async def spent_in_month(
    session: AsyncSession,
    household_id: uuid.UUID,
    month: date,
    *,
    except_cycle: uuid.UUID | None = None,
) -> int:
    """What the month's approved and locked boxes cost (skipped weeks spend nothing; a box
    waiting for its payment still counts)."""
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    cycles = [
        cycle_id
        for cycle_id in (
            await session.execute(
                select(WeeklyCycle.id).where(
                    WeeklyCycle.household_id == household_id,
                    WeeklyCycle.delivery_date >= first,
                    WeeklyCycle.delivery_date < following,
                    WeeklyCycle.status.in_(("approved", "locked", "payment_failed")),
                )
            )
        ).scalars()
        if cycle_id != except_cycle
    ]
    if not cycles:
        return 0
    lines = (await session.execute(select(BoxLine).where(BoxLine.cycle_id.in_(cycles)))).scalars()
    return sum(line.qty * line.unit_price_paise for line in lines)


async def set_budget_lock(
    session: AsyncSession, household_id: uuid.UUID, month: date, budget_paise: int | None
) -> BudgetLock | None:
    """Set the month's Budget Lock (this month or next, ≥ ₹500), or switch it off with None."""
    first = month.replace(day=1)
    this_month = IST_DATE().replace(day=1)
    next_month = (this_month + timedelta(days=32)).replace(day=1)
    if first not in (this_month, next_month):
        raise ProblemError(
            422, "subscriptions.budget_invalid", "A Budget Lock is for this month or the next."
        )
    if budget_paise is not None and budget_paise < MIN_BUDGET_PAISE:
        raise ProblemError(422, "subscriptions.budget_invalid", "At least ₹500 a month.")
    lock = await budget_lock_for(session, household_id, first)
    if budget_paise is None:
        if lock is not None:
            await session.delete(lock)
            await session.flush()
        return None
    if lock is None:
        lock = BudgetLock(household_id=household_id, month=first, budget_paise=budget_paise)
        session.add(lock)
    else:
        lock.budget_paise = budget_paise
    await session.flush()
    return lock


# --- edits, approval, skip ----------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    op: str  # remove | add | qty | substitute
    product_id: uuid.UUID
    qty: int | None = None
    substitute_id: uuid.UUID | None = None


def not_editable(cycle: WeeklyCycle) -> ProblemError:
    return ProblemError(
        409, "subscriptions.not_editable", f"A {cycle.status.replace('_', ' ')} week cannot change."
    )


def cutoff_passed() -> ProblemError:
    return ProblemError(409, "subscriptions.cutoff_passed", "The cut-off for this week has passed.")


def check_version(cycle: WeeklyCycle, expected_version: int | None) -> None:
    if expected_version is not None and cycle.version != expected_version:
        raise ProblemError(412, "subscriptions.version_conflict", "The week changed; reload it.")


def check_editable(cycle: WeeklyCycle, *, expected_version: int | None = None) -> None:
    check_version(cycle, expected_version)
    if cycle.status not in EDITABLE:
        raise not_editable(cycle)
    if now() >= cycle.cutoff_at:
        raise cutoff_passed()


def _qty(value: int | None) -> int:
    if value is None or not 1 <= value <= MAX_QTY:
        raise ProblemError(422, "subscriptions.qty_invalid", f"Quantity is 1 to {MAX_QTY} steps.")
    return value


async def _priced(
    session: AsyncSession, product_id: uuid.UUID, hub_id: uuid.UUID, code: str
) -> catalog_api.LineProduct:
    product = (await catalog_api.line_products(session, [product_id], hub_id=hub_id)).get(
        product_id
    )
    if product is None or product.price_paise is None:
        raise ProblemError(422, code, "Not a product your hub sells this week.")
    return product


async def edit_lines(
    session: AsyncSession, cycle: WeeklyCycle, ops: list[Op], *, expected_version: int
) -> list[BoxLine]:
    """Apply the customer's ops in order; every op leaves an edit_feedback row (the ranker's
    labels). With a Budget Lock on, the box must fit the month's remaining budget."""
    check_editable(cycle, expected_version=expected_version)
    lines = {line.product_id: line for line in await lines_of(session, cycle.id)}
    for op in ops:
        line = lines.get(op.product_id)
        if op.op == "add":
            product = await _priced(
                session, op.product_id, cycle.hub_id, "subscriptions.product_unknown"
            )
            qty = _qty(op.qty)
            if line is None:
                line = BoxLine(
                    cycle_id=cycle.id,
                    product_id=product.id,
                    qty=qty,
                    step_qty=product.step_qty,
                    step_unit=product.step_unit,
                    unit_price_paise=product.price_paise or 0,
                    reason_code="added_by_you",
                    reason_params=None,
                    source="customer",
                )
                session.add(line)
                lines[product.id] = line
            else:
                line.qty = qty
            action = "added"
        elif line is None:
            raise ProblemError(422, "subscriptions.product_unknown", "That item is not in the box.")
        elif op.op == "remove":
            await session.delete(line)
            del lines[op.product_id]
            action = "removed"
        elif op.op == "qty":
            qty = _qty(op.qty)
            if qty == line.qty:
                continue
            action = "qty_up" if qty > line.qty else "qty_down"
            line.qty = qty
        else:  # substitute
            if op.substitute_id is None or op.substitute_id not in await catalog_api.substitute_ids(
                session, op.product_id, cycle.hub_id
            ):
                raise ProblemError(
                    422, "subscriptions.substitute_unknown", "Pick a substitute from the list."
                )
            product = await _priced(
                session, op.substitute_id, cycle.hub_id, "subscriptions.substitute_unknown"
            )
            original = (
                await catalog_api.line_products(session, [op.product_id], hub_id=cycle.hub_id)
            ).get(op.product_id)
            qty = line.qty
            await session.delete(line)
            del lines[op.product_id]
            await session.flush()
            swapped = lines.get(product.id)
            if swapped is None:
                swapped = BoxLine(
                    cycle_id=cycle.id,
                    product_id=product.id,
                    qty=qty,
                    step_qty=product.step_qty,
                    step_unit=product.step_unit,
                    unit_price_paise=product.price_paise or 0,
                    reason_code="swapped_by_you",
                    reason_params={"from": original.slug if original else str(op.product_id)},
                    source="customer",
                    replaces_product_id=op.product_id,
                )
                session.add(swapped)
                lines[product.id] = swapped
            else:
                swapped.qty = min(MAX_QTY, swapped.qty + qty)
            action = "swapped"
        session.add(
            EditFeedback(
                cycle_id=cycle.id,
                product_id=op.product_id,
                action=action,
                substitute_product_id=op.substitute_id if action == "swapped" else None,
                at=now(),
            )
        )
        await session.flush()
    await _check_budget(session, cycle, _total(lines.values()))
    return await _saved(session, cycle, lines)


def _total(lines: Iterable[BoxLine]) -> int:
    return sum(line.qty * line.unit_price_paise for line in lines)


async def _check_budget(session: AsyncSession, cycle: WeeklyCycle, total: int) -> None:
    """With the Budget Lock on, the box must fit what the month has left."""
    lock = await budget_lock_for(session, cycle.household_id, cycle.delivery_date)
    if lock is not None:
        spent = await spent_in_month(
            session, cycle.household_id, cycle.delivery_date, except_cycle=cycle.id
        )
        if spent + total > lock.budget_paise:
            raise ProblemError(
                422,
                "subscriptions.budget_exceeded",
                "This box goes over your Budget Lock for the month.",
                extra={"over_paise": spent + total - lock.budget_paise},
            )


async def _saved(
    session: AsyncSession, cycle: WeeklyCycle, lines: dict[uuid.UUID, BoxLine]
) -> list[BoxLine]:
    """Bump the version; on an approved week the order already exists: hand the lines over."""
    cycle.version += 1
    await session.flush()
    result = sorted(lines.values(), key=lambda line: line.product_id)
    if cycle.status == "approved":
        await _hand_over(session, cycle, result)
    return result


# --- the meal swap (§3.2) -----------------------------------------------------------------------

MEAL_ACTION = {
    "next": "meal_swapped",
    "recipe": "meal_swapped",
    "tiffin": "meal_tiffin",
    "no_cook": "meal_no_cook",
    "planned": "meal_restored",
}
MEAL_KEYS = ("recipe_id", "recipe_slug", "tiffin", "sides")
NEVER_REDUCED = ("added_by_you", "daily_use")  # a swap only takes away what a meal brought


def _week_plan(meal_plan: list[dict[str, Any]], monday: date) -> list[dict[str, Any]]:
    """The cycle's meals in the planner's shape, for `planning.lines_from_plan`."""
    return [
        {
            "day": (date.fromisoformat(day["date"]) - monday).days,
            "slot": meal["slot"],
            "recipe": meal.get("recipe_slug"),
        }
        for day in meal_plan
        for meal in day["meals"]
    ]


async def _chosen_meal(
    session: AsyncSession,
    cycle: WeeklyCycle,
    day: int,
    slot: str,
    choice: str,
    recipe_id: uuid.UUID | None,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
) -> dict[str, Any]:
    """The meal `next` or `recipe` sets, under the same hard rules as generation (M1)."""
    menu = {
        r.slug: r
        for r in catalog.recipes
        if not r.is_side and r.cost_per_serving_paise is not None and slot in r.slots
    }
    monday = cycle.week_start
    festivals = await catalog_api.festivals_between(session, monday, monday + timedelta(days=6))
    request = PlanRequest.model_validate(planning.plan_request(profile, catalog, monday, festivals))
    request.recipes = [r for r in request.recipes if r.slug in menu]
    if choice == "recipe":
        recipe = next((r for r in menu.values() if r.id == recipe_id), None)
        if recipe is None:
            raise ProblemError(
                422, "subscriptions.recipe_unknown", "That dish is not on this hub's menu for it."
            )
        planned = next(r for r in request.recipes if r.slug == recipe.slug)
        broken = intel_api.rule_breaks(request, planned, day, slot, every_meal_fasts=True)
        if broken:
            raise ProblemError(
                422,
                "subscriptions.recipe_not_allowed",
                "That dish breaks your household's food rules.",
                extra={"rules": broken},
            )
    else:  # next: the cheapest allowed dish not in the week yet
        in_week = {m.get("recipe_slug") for d in cycle.meal_plan for m in d["meals"]}
        found = intel_api.next_recipe(request, day, slot, {s for s in in_week if s})
        if found is None:
            raise ProblemError(422, "subscriptions.no_alternative", "No other dish fits this meal.")
        recipe = menu[found.slug]
    return {
        "recipe_id": str(recipe.id),
        "recipe_slug": recipe.slug,
        "tiffin": False,
        "sides": list(recipe.sides),
    }


async def set_meal(
    session: AsyncSession,
    cycle: WeeklyCycle,
    *,
    on: date,
    slot: str,
    choice: str,
    recipe_id: uuid.UUID | None,
    expected_version: int,
) -> list[BoxLine]:
    """Set one meal of the week and redraw its lines: `lines_from_plan` before and after, the
    per-product difference in steps applied to the box. One meal-level edit_feedback row; the
    ingredient changes it causes are not the customer's product choices and write none."""
    check_editable(cycle, expected_version=expected_version)
    meal_plan = copy.deepcopy(cycle.meal_plan)
    day = next((d for d in meal_plan if d["date"] == on.isoformat()), None)
    meal = next((m for m in day["meals"] if m["slot"] == slot), None) if day else None
    if meal is None:
        raise ProblemError(422, "subscriptions.meal_unknown", "No such meal in this week.")
    profile = await identity_api.household_plan_profile(session, cycle.household_id)
    assert profile is not None
    catalog = await catalog_api.plan_catalog(session, cycle.hub_id)
    planned = meal.get("planned") or {key: meal.get(key) for key in MEAL_KEYS}
    if choice == "planned":
        chosen = planned
    elif choice in ("tiffin", "no_cook"):
        chosen = {"recipe_id": None, "recipe_slug": None, "tiffin": choice == "tiffin", "sides": []}
    else:
        index = (on - cycle.week_start).days
        chosen = await _chosen_meal(
            session, cycle, index, slot, choice, recipe_id, profile, catalog
        )
    before = {
        line.product_id: line
        for line in planning.lines_from_plan(
            profile, catalog, _week_plan(meal_plan, cycle.week_start), fallback=False
        )
    }
    previous_recipe = meal.get("recipe_id")
    meal.update(chosen, source="customer", planned=planned)
    after = {
        line.product_id: line
        for line in planning.lines_from_plan(
            profile, catalog, _week_plan(meal_plan, cycle.week_start), fallback=False
        )
    }

    lines = {line.product_id: line for line in await lines_of(session, cycle.id)}
    total_before = _total(lines.values())
    instead_of = {  # a product the customer substituted counts as its substitute
        line.replaces_product_id: line for line in lines.values() if line.replaces_product_id
    }
    for product_id in sorted(before.keys() | after.keys(), key=str):
        old, new = before.get(product_id), after.get(product_id)
        delta = (new.qty if new else 0) - (old.qty if old else 0)
        recipes = (new.reason_params or {}).get("recipes") if new else None
        line = lines.get(product_id) or instead_of.get(product_id)
        if line is None:
            if delta > 0 and new is not None:
                lines[product_id] = BoxLine(
                    cycle_id=cycle.id,
                    product_id=product_id,
                    qty=delta,
                    step_qty=new.step_qty,
                    step_unit=new.step_unit,
                    unit_price_paise=new.unit_price_paise,
                    reason_code="meal_plan",
                    reason_params={"recipes": recipes or []},
                    source="customer",
                )
                session.add(lines[product_id])
            continue
        if delta < 0 and line.reason_code in NEVER_REDUCED:
            continue
        line.qty += delta
        if line.qty <= 0:
            await session.delete(line)
            del lines[line.product_id]
        elif line.reason_code == "meal_plan" and recipes is not None:
            line.reason_params = {**(line.reason_params or {}), "recipes": recipes}
    total = _total(lines.values())
    if total > total_before:  # tiffin, no-cook and cheaper swaps always go through
        await _check_budget(session, cycle, total)
    cycle.meal_plan = meal_plan
    session.add(
        EditFeedback(
            cycle_id=cycle.id,
            product_id=None,
            action=MEAL_ACTION[choice],
            meal_date=on,
            slot=slot,
            recipe_id=uuid.UUID(previous_recipe) if previous_recipe else None,
            substitute_recipe_id=uuid.UUID(chosen["recipe_id"]) if chosen["recipe_id"] else None,
            at=now(),
        )
    )
    return await _saved(session, cycle, lines)


async def mark_tiffin_meal(
    session: AsyncSession, household_id: uuid.UUID, on: date, slot: str, *, booked: bool
) -> bool:
    """A tiffin booked for a meal sets that meal to `tiffin` (its ingredients leave the box), and
    a cancelled one sets it back to the plan, exactly as `PUT /v1/cycles/{id}/meals` does, while
    the week can still be edited (M4 §3.5). Returns whether the week changed."""
    cycle = (
        await session.execute(
            select(WeeklyCycle)
            .where(
                WeeklyCycle.household_id == household_id,
                WeeklyCycle.week_start <= on,
                WeeklyCycle.week_start > on - timedelta(days=7),
                WeeklyCycle.status.in_(EDITABLE),
            )
            .order_by(WeeklyCycle.delivery_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    day = next((d for d in cycle.meal_plan if d["date"] == on.isoformat()), None) if cycle else None
    meal = next((m for m in day["meals"] if m["slot"] == slot), None) if day else None
    if cycle is None or meal is None or bool(meal.get("tiffin")) == booked:
        return False
    if not booked and meal.get("source") != "customer":
        return False
    try:
        async with session.begin_nested():
            await set_meal(
                session,
                cycle,
                on=on,
                slot=slot,
                choice="tiffin" if booked else "planned",
                recipe_id=None,
                expected_version=cycle.version,
            )
    except ProblemError:  # past the cut-off, or over the Budget Lock: the box stays as it is
        return False
    return True


def approved_cycle(cycle: WeeklyCycle, lines: list[BoxLine]) -> orders_api.ApprovedCycle:
    return orders_api.ApprovedCycle(
        cycle_id=cycle.id,
        household_id=cycle.household_id,
        hub_id=cycle.hub_id,
        society_id=cycle.society_id,
        delivery_date=cycle.delivery_date,
        slot_id=cycle.slot_id,
        lines=tuple(
            orders_api.CycleLine(
                line.product_id,
                line.qty,
                line.unit_price_paise,
                line.reason_code,
                line.reason_params,
            )
            for line in lines
        ),
        data_source=cycle.data_source,
        version=cycle.version,
    )


async def _hand_over(
    session: AsyncSession,
    cycle: WeeklyCycle,
    lines: list[BoxLine],
    *,
    auto_approved: bool = False,
) -> None:
    """Create (or refresh) the M4 order and publish `box_approved` with the same lines.

    `auto_approved` is true only on the system's approval itself, so M11 tells the customer
    once; an edit to that box afterwards sends the event again without it."""
    if not lines:
        raise ProblemError(422, "subscriptions.qty_invalid", "The box is empty; add something.")
    handed = approved_cycle(cycle, lines)
    order = await orders_api.create_from_cycle(session, handed)
    cycle.order_id = order.id
    await publish(
        session,
        events.BOX_APPROVED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(cycle.household_id),
            "hub_id": str(cycle.hub_id),
            "society_id": str(cycle.society_id),
            "delivery_date": cycle.delivery_date.isoformat(),
            "slot_id": str(cycle.slot_id),
            "version": cycle.version,
            "lines": [
                {
                    "product_id": str(line.product_id),
                    "qty": line.qty,
                    "unit_price_paise": line.unit_price_paise,
                    "reason_code": line.reason_code,
                    "reason_params": line.reason_params,
                }
                for line in handed.lines
            ],
            "total_paise": sum(line.qty * line.unit_price_paise for line in handed.lines),
            "auto_approved": auto_approved,
        },
    )


async def approve(
    session: AsyncSession,
    cycle: WeeklyCycle,
    *,
    expected_version: int | None,
    actor: str = "customer",
) -> WeeklyCycle:
    """Approve before the cut-off: the box becomes an M4 order with these lines and prices.
    Approving an approved week again changes nothing."""
    check_editable(cycle, expected_version=expected_version)
    if cycle.status == "approved":
        return cycle
    cycle.status = "approved"
    cycle.approved_at = now()
    cycle.auto_approved = actor == "system"
    cycle.version += 1
    await session.flush()
    await _hand_over(
        session, cycle, await lines_of(session, cycle.id), auto_approved=cycle.auto_approved
    )
    await session.flush()
    return cycle


async def skip(session: AsyncSession, cycle: WeeklyCycle) -> WeeklyCycle:
    """Skip the week before the cut-off, or a `payment_failed` week until its grace ends
    (§3.3); an order already created is cancelled (M4)."""
    if cycle.status == "skipped":
        return cycle
    if cycle.status == "payment_failed":
        if now() >= orders_api.grace_until_for(cycle.delivery_date):
            raise cutoff_passed()
    else:
        check_editable(cycle)
    await orders_api.cancel_from_cycle(session, cycle.id)
    cycle.status = "skipped"
    cycle.version += 1
    await session.flush()
    await publish(
        session,
        events.BOX_SKIPPED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(cycle.household_id),
            "delivery_date": cycle.delivery_date.isoformat(),
        },
    )
    return cycle


async def unskip(
    session: AsyncSession, cycle: WeeklyCycle, *, expected_version: int
) -> WeeklyCycle:
    """A skipped week back to approval before its cut-off (§3.3), with the lines it had when it
    was skipped: `awaiting_approval`, or `draft` if the box-ready notice never went out.

    The skip cancelled its order, so approving it again creates a new one (M4 keeps `cycle_id`
    unique only among orders that are not cancelled); the Budget Lock counts it once approved.
    """
    check_version(cycle, expected_version)
    if cycle.status != "skipped":
        raise ProblemError(409, "subscriptions.not_skipped", "Only a skipped week can come back.")
    if now() >= cycle.cutoff_at:
        raise cutoff_passed()
    subscription = await session.get(Subscription, cycle.subscription_id)
    if subscription is not None and is_paused_on(subscription, cycle.delivery_date):
        raise ProblemError(
            409, "subscriptions.paused", "Your subscription is paused that week; resume it first."
        )
    cycle.status = "awaiting_approval" if cycle.notice_sent_at is not None else "draft"
    cycle.order_id = None
    cycle.approved_at = None
    cycle.auto_approved = False
    cycle.version += 1
    await session.flush()
    await publish(
        session,
        events.BOX_UNSKIPPED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(cycle.household_id),
            "delivery_date": cycle.delivery_date.isoformat(),
        },
    )
    return cycle


@dataclass(frozen=True)
class PaymentDue:
    order_id: uuid.UUID
    total_paise: int
    wallet_paise: int
    shortfall_paise: int
    due_at: datetime


async def payment_due(session: AsyncSession, cycle: WeeklyCycle) -> PaymentDue | None:
    """What a `payment_failed` week still needs, with the wallet read live (§3.3); None for any
    other week, or once the order has moved on (paid or cancelled) before the cycle caught up."""
    if cycle.status != "payment_failed" or cycle.order_id is None:
        return None
    order = await orders_api.get(session, cycle.order_id)
    if order.status != "payment_failed":
        return None
    owner = await identity_api.owner_of_household(session, cycle.household_id)
    wallet = 0 if owner is None else await billing_api.balance(session, owner)
    return PaymentDue(
        order_id=order.id,
        total_paise=order.total_paise,
        wallet_paise=wallet,
        shortfall_paise=max(0, order.total_paise - wallet),
        due_at=orders_api.grace_until_for(cycle.delivery_date),
    )


async def mark_payment_failed(session: AsyncSession, cycle: WeeklyCycle) -> None:
    """The cut-off could not debit the box: the cycle mirrors it and shows what is due. Only an
    approved week moves; a late event after `orders.locked` or a skip changes nothing."""
    if cycle.status == "approved":
        cycle.status = "payment_failed"
        cycle.version += 1
        await session.flush()


async def mark_locked(session: AsyncSession, cycle: WeeklyCycle, order_id: uuid.UUID) -> None:
    """M4 locked the order at the cut-off, after a top-up or on `pay`: the cycle mirrors it."""
    if cycle.status in (*EDITABLE, "payment_failed"):
        cycle.status = "locked"
        cycle.order_id = order_id
        cycle.version += 1
        await session.flush()


# --- the time-of-day notices and auto-approve (spec §6) -----------------------------------------


def auto_approvable() -> ColumnElement[bool]:
    """What `apply_auto_approve` takes: the household asked for it and the box-ready notice went
    out at least 24 h before the cut-off (the pre-debit notice rule, handbook 06). Needs the
    query joined to `Subscription`."""
    return and_(
        Subscription.auto_approve.is_(True),
        WeeklyCycle.notice_sent_at.is_not(None),
        WeeklyCycle.notice_sent_at <= WeeklyCycle.cutoff_at - timedelta(hours=24),
    )


async def _totals(session: AsyncSession, cycle_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(BoxLine.cycle_id, func.sum(BoxLine.qty * BoxLine.unit_price_paise))
        .where(BoxLine.cycle_id.in_(cycle_ids))
        .group_by(BoxLine.cycle_id)
    )
    return {cycle_id: int(total) for cycle_id, total in rows}


def _notice(cycle: WeeklyCycle, total_paise: int) -> dict[str, Any]:
    """The payload of `box_ready` and `cutoff_reminder` (spec §5): what M11's message says."""
    return {
        "cycle_id": str(cycle.id),
        "household_id": str(cycle.household_id),
        "delivery_date": cycle.delivery_date.isoformat(),
        "cutoff_at": cycle.cutoff_at.isoformat(),
        "total_paise": total_paise,
    }


async def send_box_ready_notices(
    session: AsyncSession, delivery_date: date, at: datetime
) -> dict[str, int]:
    """The `send_box_ready_notices` job's body: `draft` → `awaiting_approval`, `notice_sent_at`
    and one `subscriptions.box_ready` per cycle. Only drafts are taken, so a rerun sends nothing
    twice. An earlier delivery day's draft still before its cut-off is taken too: a generation
    run that came after its notice run would otherwise leave that box a draft for good.

    The version stays: the box the customer may now edit is the one generated."""
    cycles = list(
        (
            await session.execute(
                select(WeeklyCycle)
                .where(
                    WeeklyCycle.status == "draft",
                    WeeklyCycle.delivery_date <= delivery_date,
                    WeeklyCycle.cutoff_at > at,
                )
                .order_by(WeeklyCycle.delivery_date, WeeklyCycle.id)
            )
        ).scalars()
    )
    totals = await _totals(session, [cycle.id for cycle in cycles])
    for cycle in cycles:
        # The real moment, not `at`: auto-approve measures the 24 h from when it went out.
        cycle.notice_sent_at = now()
        cycle.status = "awaiting_approval"
        await publish(session, events.BOX_READY, _notice(cycle, totals.get(cycle.id, 0)))
    await session.flush()
    return {"sent": len(cycles)}


async def remind_unapproved(
    session: AsyncSession, delivery_date: date, at: datetime
) -> dict[str, int]:
    """The `remind_unapproved` job's body: one `subscriptions.cutoff_reminder` per cycle still
    `awaiting_approval` that `apply_auto_approve` will not take; `reminded_at` keeps a rerun
    from sending it twice. A household on auto-approve whose notice came too late to count is
    reminded too, since nothing else will approve its box."""
    cycles = list(
        (
            await session.execute(
                select(WeeklyCycle)
                .join(Subscription, Subscription.id == WeeklyCycle.subscription_id)
                .where(
                    WeeklyCycle.delivery_date == delivery_date,
                    WeeklyCycle.status == "awaiting_approval",
                    WeeklyCycle.reminded_at.is_(None),
                    WeeklyCycle.cutoff_at > at,
                    not_(auto_approvable()),
                )
                .order_by(WeeklyCycle.id)
            )
        ).scalars()
    )
    totals = await _totals(session, [cycle.id for cycle in cycles])
    for cycle in cycles:
        cycle.reminded_at = now()
        await publish(session, events.CUTOFF_REMINDER, _notice(cycle, totals.get(cycle.id, 0)))
    await session.flush()
    return {"reminded": len(cycles)}


async def auto_approve_due(session: AsyncSession, delivery_date: date) -> dict[str, int]:
    """The `apply_auto_approve` job's body: approve what the household asked us to, when the
    box-ready notice went out at least 24 h before the cut-off."""
    rows = await session.execute(
        select(WeeklyCycle)
        .join(Subscription, Subscription.id == WeeklyCycle.subscription_id)
        .where(
            WeeklyCycle.delivery_date == delivery_date,
            WeeklyCycle.status == "awaiting_approval",
            auto_approvable(),
        )
        .order_by(WeeklyCycle.id)
    )
    counts = {"approved": 0, "failed": 0}
    for cycle in rows.scalars().all():
        try:
            async with session.begin_nested():
                await approve(session, cycle, expected_version=None, actor="system")
        except ProblemError as exc:  # a full slot, an empty box: the customer decides by hand
            counts["failed"] += 1
            log.warning("auto_approve_failed", cycle_id=str(cycle.id), code=exc.code)
            continue
        counts["approved"] += 1
    return counts


# --- pantry feedback ----------------------------------------------------------------------------


async def record_feedback(
    session: AsyncSession,
    cycle: WeeklyCycle,
    items: dict[uuid.UUID, str],
    *,
    today: date,
) -> int:
    """Ran out / wasted / just right per item, from delivery day for 14 days; one row per
    product, the latest tap wins."""
    window_open = cycle.status not in (
        "skipped",
        "cancelled",
    ) and cycle.delivery_date <= today <= cycle.delivery_date + timedelta(days=FEEDBACK_DAYS)
    if not window_open:
        raise ProblemError(
            422,
            "subscriptions.feedback_window_closed",
            f"Tell us from delivery day for {FEEDBACK_DAYS} days.",
        )
    on_box = {line.product_id for line in await lines_of(session, cycle.id)}
    unknown = [str(pid) for pid in items if pid not in on_box]
    if unknown:
        raise ProblemError(
            422,
            "subscriptions.product_unknown",
            "That item was not in this box.",
            extra={"product_ids": unknown},
        )
    existing = {
        row.product_id: row
        for row in (
            await session.execute(
                select(PantryFeedback).where(
                    PantryFeedback.cycle_id == cycle.id, PantryFeedback.product_id.in_(items)
                )
            )
        ).scalars()
    }
    for product_id, signal in items.items():
        row = existing.get(product_id)
        if row is None:
            session.add(
                PantryFeedback(
                    cycle_id=cycle.id,
                    household_id=cycle.household_id,
                    product_id=product_id,
                    signal=signal,
                    at=now(),
                )
            )
        else:
            row.signal = signal
            row.at = now()
    await session.flush()
    return len(items)


async def end_for_deletion(session: AsyncSession, household_id: uuid.UUID) -> None:
    """The owner asked to delete the account (M1 §3.5): the subscription ends, every week still
    before its cut-off is skipped (its order cancelled), and so is a `payment_failed` week. Paid
    weeks are delivered as normal. No `box_skipped` is published: nobody is told."""
    subscription = await subscription_of(session, household_id)
    if subscription is not None:
        subscription.status = "ended"
    cycles = await session.scalars(
        select(WeeklyCycle)
        .where(
            WeeklyCycle.household_id == household_id,
            WeeklyCycle.status.in_(("draft", *EDITABLE, "payment_failed")),
        )
        .with_for_update()
    )
    for cycle in cycles.all():
        if cycle.status != "payment_failed" and now() >= cycle.cutoff_at:
            continue  # the cut-off job takes it from here
        await orders_api.cancel_from_cycle(session, cycle.id)
        cycle.status = "skipped"
        cycle.version += 1
    await session.flush()


async def delete_for_user(session: AsyncSession, household_id: uuid.UUID) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 7): the subscription, cycles with their lines and meal plans,
    Budget Locks and both feedback tables. Forecasting learns from orders, which M4 keeps."""
    cycles = select(WeeklyCycle.id).where(WeeklyCycle.household_id == household_id)
    counts = {}
    for model, which in (
        (EditFeedback, EditFeedback.cycle_id.in_(cycles)),
        (PantryFeedback, PantryFeedback.household_id == household_id),
        (BoxLine, BoxLine.cycle_id.in_(cycles)),
        (WeeklyCycle, WeeklyCycle.household_id == household_id),
        (BudgetLock, BudgetLock.household_id == household_id),
        (Subscription, Subscription.household_id == household_id),
    ):
        gone = await session.execute(delete(model).where(which))
        counts[model.__tablename__] = gone.rowcount  # pyright: ignore[reportAttributeAccessIssue]
    return counts
