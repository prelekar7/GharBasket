"""What the ML service may see for `track_order`, `get_tiffin_menus` and `book_tiffin` (M13 §3.1).

The household's current orders and the tiffin menus it may still book go out as two optional fields
(next to `today`), with opaque per-turn handles (`O1`, `T1`, …) and nothing that identifies a
person, an order, a menu, a kitchen or an address. Each part is built inside a savepoint: if one
fails the field is left out, the ML service says it cannot see that, and the turn goes on.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx2
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.locale import Language

log = structlog.get_logger("gharbasket.assistant")
NEW_FIELDS = ("today", "orders", "tiffin")  # what an older ML service rejects (§3.1)


def error_locations(response: httpx2.Response) -> list[str]:
    """Where a 422 from the ML service points (`body.today`, `body.messages.0.content`), never the
    values; empty when the answer is not FastAPI's validation error."""
    try:
        detail = response.json()["detail"]
        return [".".join(str(p) for p in e["loc"]) for e in detail]
    except (ValueError, KeyError, TypeError):
        return []


def refused_only_new_fields(response: httpx2.Response) -> bool:
    """Whether an ML service from before M13 §3.1 said no: every error in its 422 is an extra
    field forbidden at `body.today`, `body.orders` or `body.tiffin`. Any other 422 (schema drift
    of the new service) is not an old service and must not drop the tools."""
    try:
        detail = response.json()["detail"]
        return bool(detail) and all(
            e["type"] == "extra_forbidden"
            and len(e["loc"]) == 2
            and e["loc"][0] == "body"
            and e["loc"][1] in NEW_FIELDS
            for e in detail
        )
    except (ValueError, KeyError, TypeError):
        return False


@dataclass
class ToolContext:
    """The new request fields, and the menus behind the `T` handles (kept in memory, per turn)."""

    fields: dict[str, Any]
    menus: dict[str, tuple[orders_api.TiffinChoice, orders_api.MenuChoice]] = field(
        default_factory=dict
    )
    choices: list[orders_api.TiffinChoice] = field(default_factory=list)


async def _safely[T](
    session: AsyncSession, what: str, build: Callable[[], Awaitable[T]]
) -> T | None:
    try:
        async with session.begin_nested():
            return await build()
    except Exception:
        log.exception("assistant_context_failed", part=what)
        return None


async def order_entries(
    session: AsyncSession, household_id: uuid.UUID, today: date
) -> list[dict[str, Any]]:
    orders = await orders_api.household_orders(session, household_id, today)
    signals = await logistics_api.delivery_signals(session, orders)
    slots: dict[uuid.UUID, orders_api.SlotRef | None] = {}
    entries: list[dict[str, Any]] = []
    for number, order in enumerate(orders, start=1):
        if order.slot_id not in slots:
            slots[order.slot_id] = await orders_api.slot_ref(session, order.slot_id)
        slot, signal = slots[order.slot_id], signals[order.id]
        entries.append(
            {
                "handle": f"O{number}",
                "kind": order.kind,
                "status": order.status,
                "delivery_date": order.delivery_date.isoformat(),
                "meal": slot.meal if slot is not None and order.kind == "tiffin" else None,
                "window": None
                if signal.window is None
                else {"start": signal.window[0], "end": signal.window[1]},
                "eta": signal.eta,
                "on_the_way": signal.on_the_way,
            }
        )
    return entries


def tiffin_entries(
    choices: list[orders_api.TiffinChoice],
) -> tuple[dict[str, Any], dict[str, tuple[orders_api.TiffinChoice, orders_api.MenuChoice]]]:
    handles: dict[str, tuple[orders_api.TiffinChoice, orders_api.MenuChoice]] = {}
    options: list[dict[str, Any]] = []
    for choice in choices:
        menus = []
        for menu in choice.menus:
            handle = f"T{len(handles) + 1}"
            handles[handle] = (choice, menu)
            menus.append(
                {
                    "handle": handle,
                    "dishes": list(menu.dishes)[:10],
                    "price_paise": menu.price_paise,
                }
            )
        options.append(
            {
                "date": choice.on_date.isoformat(),
                "meal": choice.meal,
                "order_by": choice.order_by.isoformat(),
                "booked": choice.booked,
                "menus": menus,
            }
        )
    return {"options": options}, handles


async def build(
    session: AsyncSession,
    household_id: uuid.UUID,
    hub_id: uuid.UUID | None,
    lang: Language,
    today: date,
) -> ToolContext:
    """`today`, the household's `orders` and the bookable `tiffin` menus (any of the two last may
    be missing: no hub, or the part failed)."""
    context = ToolContext({"today": today.isoformat()})
    orders = await _safely(session, "orders", lambda: order_entries(session, household_id, today))
    if orders is not None:
        context.fields["orders"] = orders
    if hub_id is not None:
        hub = hub_id
        found = await _safely(
            session, "tiffin", lambda: orders_api.tiffin_choices(session, household_id, hub, lang)
        )
        if found is not None:
            context.choices = found
            context.fields["tiffin"], context.menus = tiffin_entries(found)
    return context
