"""Green slots (M3 §3.6, contract in M10 §3.8): quote every box slot of a cycle's delivery date with
the saving a move there earns, and move the box at a quoted saving.

M10 prices the route (`intel_gateway.api.price_slots`, a route cost, never a price); this module
turns a cost into a saving with the hub's rules. All amounts are integer paise.
"""

import uuid
from datetime import timedelta
from typing import Any

import httpx2
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import SlotQuote, WeeklyCycle
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.settings import Settings

QUOTE_LIFE = timedelta(minutes=15)
MIN_SAVING_PAISE = 500  # no "save ₹2"
GREEN_REASONS = ("SAME_SOCIETY", "NEIGHBOUR_STOP")
NOT_EXPECTED = ("skipped", "cancelled")  # cycles whose boxes ride on no route


def saving_paise(
    current_cost: int | None,
    slot_cost: int | None,
    *,
    share_pct: int,
    max_paise: int,
    total_paise: int,
) -> int:
    """What a move off the current slot saves (M10 §3.8): the hub's share of the route saving, at
    most its cap and the lines' total, in whole rupees, and 0 below ₹5. Never negative, never a
    surcharge; 0 when either cost is unknown (an infeasible slot, or the current one unpriced)."""
    if current_cost is None or slot_cost is None:
        return 0
    raw = max(0, current_cost - slot_cost) * share_pct // 100
    saving = max(0, min(raw, max_paise, total_paise)) // 100 * 100
    return 0 if saving < MIN_SAVING_PAISE else saving


def require_on(settings: Settings) -> None:
    """The kill switch (`GREEN_SLOTS_ENABLED`): off, both endpoints are not there."""
    if not settings.green_slots_enabled:
        raise ProblemError(404, "subscriptions.green_slots_off", "Green slots are not on yet.")


def _metres(nearest_m: int | None, reason: str) -> dict[str, int] | None:
    if nearest_m is None or reason not in ("NEIGHBOUR_STOP", "NEW_STOP"):
        return None
    return {"metres": (nearest_m + 50) // 100 * 100}  # rounded to 100 m; never a name


async def _expected(
    session: AsyncSession, cycle: WeeklyCycle
) -> dict[tuple[uuid.UUID, uuid.UUID], int]:
    """Boxes the other cycles of that hub and date expect, by (society, slot): not this one."""
    rows = await session.execute(
        select(WeeklyCycle.society_id, WeeklyCycle.slot_id, func.count())
        .where(
            WeeklyCycle.hub_id == cycle.hub_id,
            WeeklyCycle.delivery_date == cycle.delivery_date,
            WeeklyCycle.status.not_in(NOT_EXPECTED),
            WeeklyCycle.id != cycle.id,
        )
        .group_by(WeeklyCycle.society_id, WeeklyCycle.slot_id)
    )
    return {(society, slot): int(n) for society, slot, n in rows.all()}


async def _prices(
    session: AsyncSession, http: httpx2.AsyncClient, settings: Settings, cycle: WeeklyCycle
) -> intel_api.SlotPrices | None:
    """The household's society priced into the day's tentative routes, one box; None (no ML call)
    when the society has no point or the hub has no tentative day."""
    point = (await community_api.society_points(session, [cycle.society_id])).get(cycle.society_id)
    if point is None:
        return None
    day = await logistics_api.tentative_day(
        session, cycle.hub_id, cycle.delivery_date, expected=await _expected(session, cycle)
    )
    if day is None or not day.slots:
        return None
    candidate = intel_api.SlotCandidate(id=cycle.society_id, lat=point[0], lng=point[1])
    return await intel_api.price_slots(
        http,
        settings,
        day,
        [candidate],
        session=session,
        subject_type="household",
        subject_id=cycle.household_id,
    )


async def _items(
    session: AsyncSession,
    cycle: WeeklyCycle,
    slots: list[orders_api.SlotAvailability],
    prices: intel_api.SlotPrices | None,
    total_paise: int,
) -> list[dict[str, Any]]:
    priced = {} if prices is None else {p.id: p for c in prices.candidates for p in c.slots}
    hub = await hub_ops_api.get_hub(session, cycle.hub_id)
    share, cap = (0, 0) if hub is None else (hub.green_saving_share_pct, hub.green_saving_max_paise)
    mine = priced.get(str(cycle.slot_id))
    current_cost = mine.marginal_cost_paise if mine is not None and mine.feasible else None
    items = []
    for slot in slots:
        price = priced.get(str(slot.id))
        current = slot.id == cycle.slot_id
        item: dict[str, Any] = {
            "slot_id": str(slot.id),
            "available": slot.available,
            "green": False,
            "saving_paise": 0,
            "rank": None,
            "reason_code": "unpriced",
            "reason_params": None,
            "marginal_cost_paise": None,  # kept in the quote; never in the response
        }
        if price is not None and price.feasible:
            item |= {
                "green": price.reason in GREEN_REASONS,
                "saving_paise": 0
                if current
                else saving_paise(
                    current_cost,
                    price.marginal_cost_paise,
                    share_pct=share,
                    max_paise=cap,
                    total_paise=total_paise,
                ),
                "rank": price.rank,
                "reason_code": price.reason.lower(),
                "reason_params": _metres(price.nearest_stop_m, price.reason),
                "marginal_cost_paise": price.marginal_cost_paise,
            }
        items.append(item)
    return items


async def quote(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    cycle: WeeklyCycle,
    *,
    total_paise: int,
) -> tuple[SlotQuote, bool]:
    """The cycle's quote that has not expired (no ML call), or a new one; True when new."""
    service.check_editable(cycle)
    at = service.now()
    existing = await session.scalar(
        select(SlotQuote)
        .where(
            SlotQuote.cycle_id == cycle.id,
            SlotQuote.expires_at > at,
            SlotQuote.current_slot_id == cycle.slot_id,  # a move since makes it stale
        )
        .order_by(SlotQuote.expires_at.desc())
        .limit(1)
    )
    if existing is not None:
        return existing, False
    slots = await orders_api.box_slots(session, cycle.hub_id, cycle.delivery_date)
    prices = await _prices(session, http, settings, cycle) if slots else None
    made = SlotQuote(
        cycle_id=cycle.id,
        household_id=cycle.household_id,
        delivery_date=cycle.delivery_date,
        current_slot_id=cycle.slot_id,
        items=await _items(session, cycle, slots, prices, total_paise),
        source="unpriced" if prices is None else prices.source,
        created_at=at,
        expires_at=at + QUOTE_LIFE,
    )
    session.add(made)
    await session.flush()
    return made, True


def quote_item(quote: SlotQuote, slot_id: uuid.UUID) -> dict[str, Any] | None:
    return next((i for i in quote.items if i["slot_id"] == str(slot_id)), None)


async def move(
    session: AsyncSession,
    cycle: WeeklyCycle,
    *,
    slot_id: uuid.UUID,
    quote_id: uuid.UUID,
    expected_version: int,
) -> WeeklyCycle:
    """Move this week's box to a quoted slot at the quote's saving (M10 §3.8): kept on the cycle
    until the cut-off, whatever later bookings do. An approved box's order moves with it."""
    service.check_editable(cycle, expected_version=expected_version)
    found = await session.get(SlotQuote, quote_id)
    if found is None or found.cycle_id != cycle.id:
        raise ProblemError(422, "subscriptions.quote_invalid", "That is not this week's quote.")
    if service.now() >= found.expires_at or found.current_slot_id != cycle.slot_id:
        raise ProblemError(
            409, "subscriptions.quote_expired", "The quote has expired; ask for a new one."
        )
    item = quote_item(found, slot_id)
    if item is None:
        raise ProblemError(422, "subscriptions.quote_invalid", "That slot is not in the quote.")
    if slot_id == cycle.slot_id:
        return cycle  # already there: the saving it has stays
    if cycle.status != "approved":  # approval enforces capacity; an order moves under M4's lock
        room = {
            s.id: s.available
            for s in await orders_api.box_slots(session, cycle.hub_id, cycle.delivery_date)
        }
        if room.get(slot_id, 0) <= 0:
            raise ProblemError(
                409,
                "orders.slot_full",
                "This slot is full for that date.",
                extra={"slot_id": str(slot_id)},
            )
    cycle.slot_id = slot_id
    cycle.slot_quote_id = found.id
    cycle.slot_saving_paise = int(item["saving_paise"])
    cycle.version += 1
    await session.flush()
    if cycle.status == "approved":
        await service.hand_over(session, cycle, await service.lines_of(session, cycle.id))
        await session.flush()
    return cycle


async def green_slot(session: AsyncSession, cycle: WeeklyCycle) -> dict[str, Any] | None:
    """The cycle's applied move: its quote, saving and reason; None without one."""
    if cycle.slot_quote_id is None:
        return None
    found = await session.get(SlotQuote, cycle.slot_quote_id)
    item = None if found is None else quote_item(found, cycle.slot_id)
    return {
        "quote_id": cycle.slot_quote_id,
        "saving_paise": cycle.slot_saving_paise,
        "reason_code": "unpriced" if item is None else item["reason_code"],
    }
