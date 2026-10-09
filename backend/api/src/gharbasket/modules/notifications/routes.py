"""Which events become a message, for whom, with which values (spec §3.1 event map).

One small function per mapped event. Household events go to the household's owner, the person
whose wallet pays; billing and account events name their `user_id`. Each event's raw values are
renamed to the placeholders of its kind (`kinds.py`) and only those are kept, so a message never
stores more of an event than its text shows.

A payload that is missing a value or holds one of the wrong type raises KeyError, ValueError or
TypeError here; the consumer logs it and moves on (`jobs.py`).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.notifications.kinds import KINDS
from gharbasket.modules.notifications.render import format_value

# Event field → the placeholders it fills.
RENAMED: dict[str, tuple[str, ...]] = {
    "total_paise": ("total",),
    "delivery_date": ("day",),
    "cutoff_at": ("cutoff_day", "cutoff_time"),
    "shortfall_paise": ("shortfall",),
    "grace_until": ("grace_time",),
    "refund_paise": ("refund",),
    "credit_paise": ("credit",),
    "amount_paise": ("amount",),
}


@dataclass(frozen=True)
class Route:
    kind: str  # a key of kinds.KINDS
    user_ids: list[uuid.UUID]
    params: dict[str, Any]


RouteFn = Callable[[AsyncSession, dict[str, Any]], Awaitable[Route | None]]


def params_for(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The kind's placeholders from the payload; each must be there and readable."""
    renamed = {new: value for old, value in payload.items() for new in RENAMED.get(old, ())}
    placeholders = KINDS[kind].placeholders
    params = {name: renamed[name] for name in placeholders}  # KeyError when one is missing
    for name, value in params.items():
        format_value(placeholders[name], value, "en")  # fails now rather than on every read
    return params


async def _to_owner(session: AsyncSession, kind: str, payload: dict[str, Any]) -> Route | None:
    household_id = uuid.UUID(str(payload["household_id"]))
    params = params_for(kind, payload)
    owner = await identity_api.owner_of_household(session, household_id)
    return None if owner is None else Route(kind, [owner], params)


def _to_user(kind: str, payload: dict[str, Any]) -> Route:
    return Route(kind, [uuid.UUID(str(payload["user_id"]))], params_for(kind, payload))


def to_owner(kind: str) -> RouteFn:
    """A household event that always sends: the box is ready, it is on its way …"""

    async def route(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
        return await _to_owner(session, kind, payload)

    return route


async def box_approved(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """Only Autopilot's own approvals: the customer knows what they approved themselves."""
    if payload.get("auto_approved") is not True:
        return None
    return await _to_owner(session, "subscriptions.box_approved", payload)


async def cancelled(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """Only a cancellation for an unpaid box; the others are the customer's own doing."""
    if payload.get("reason") != "payment":
        return None
    return await _to_owner(session, "orders.cancelled", payload)


async def shortage_substituted(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """The event names its household from B4 on; before that there is no one to tell."""
    if "household_id" not in payload:
        return None
    return await _to_owner(session, "orders.shortage_substituted", payload)


async def issue_resolved(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    outcome = "credited" if payload.get("status") == "credited" else "rejected"
    return await _to_owner(session, f"orders.issue_resolved.{outcome}", payload)


async def wallet_topped_up(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    return _to_user("billing.wallet_topped_up", payload)


async def data_export_ready(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """Opens Profile; never the download link (M1 §3.3)."""
    return _to_user("identity.data_export_ready", payload)


ROUTES: dict[str, RouteFn] = {
    "subscriptions.box_ready": to_owner("subscriptions.box_ready"),
    "subscriptions.cutoff_reminder": to_owner("subscriptions.cutoff_reminder"),
    "subscriptions.box_approved": box_approved,
    "orders.locked": to_owner("orders.locked"),
    "orders.payment_failed": to_owner("orders.payment_failed"),
    "orders.cancelled": cancelled,
    "orders.dispatched": to_owner("orders.dispatched"),
    "orders.at_society": to_owner("orders.at_society"),
    "orders.delivered": to_owner("orders.delivered"),
    "orders.delivery_failed": to_owner("orders.delivery_failed"),
    "orders.shortage_substituted": shortage_substituted,
    "orders.issue_resolved": issue_resolved,
    "billing.wallet_topped_up": wallet_topped_up,
    "identity.data_export_ready": data_export_ready,
}
