"""Which events become a message, for whom, with which values (spec §3.1 event map).

One small function per mapped event. Household events go to the household's owner, the person
whose wallet pays; billing, account and partner events name their `user_id`. Each event's raw
values are renamed to the placeholders of its kind (`kinds.py`) and only those are kept, so a
message never stores more of an event than its text shows.

A payload that is missing a value or holds one of the wrong type raises KeyError, ValueError or
TypeError here; the consumer logs it and moves on (`jobs.py`).
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.notifications.kinds import KINDS
from gharbasket.modules.notifications.render import format_value
from gharbasket.platform.settings import get_settings

log = structlog.get_logger("gharbasket.notifications")

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
    "points": ("points",),
}


@dataclass(frozen=True)
class Route:
    kind: str  # a key of kinds.KINDS
    user_ids: list[uuid.UUID]
    params: dict[str, Any]
    # Not sent when the person got this very message (same kind and values) in the last two
    # days from another event: for an event that M7 may publish again without a marker.
    skip_repeat: bool = False
    # The app link when it names a record (e.g. `/society/<id>`); else the kind's own.
    link: str | None = None


# One event may tell several kinds of people, each in its own words.
RouteFn = Callable[[AsyncSession, dict[str, Any]], Awaitable[Route | list[Route] | None]]


def checked(kind: str, values: dict[str, Any]) -> dict[str, Any]:
    """The kind's placeholders from `values`; each must be there and readable."""
    placeholders = KINDS[kind].placeholders
    params = {name: values[name] for name in placeholders}  # KeyError when one is missing
    for name, value in params.items():
        format_value(placeholders[name], value, "en")  # fails now rather than on every read
    return params


def params_for(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The kind's placeholders from the payload's fields, renamed as `RENAMED` says."""
    return checked(
        kind, {new: value for old, value in payload.items() for new in RENAMED.get(old, ())}
    )


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


async def drift_detected(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """A model's forecast began to drift (M10 §6, once per episode): to every admin, naming the hub
    and the data source (`synthetic` drift on staging is not a pilot problem). Only the two WAPE
    values are kept; the full numbers are on the AI health page. A hub that no longer exists is
    logged and not told: there is no name to give in the reader's language. The link is the
    console's absolute URL when CONSOLE_URL is set, so a push opens the browser."""
    admins = await identity_api.users_with_role(session, "admin")
    if not admins:
        return None
    hub_id = uuid.UUID(str(payload["hub_id"]))
    hub = await hub_ops_api.get_hub(session, hub_id)
    if hub is None:
        log.warning("drift_hub_missing", hub_id=str(hub_id))
        return None
    values = {
        "hub": hub.name,
        "source": payload["data_source"],
        "live": payload["live"],
        "backtest": payload["backtest"],
    }
    kind = "intel.drift_detected"
    return Route(kind, admins, checked(kind, values), link=console_link(kind))


def console_link(kind: str) -> str | None:
    """The kind's console page as an absolute URL when CONSOLE_URL is set, so a staff member's
    push opens the browser; else None, and the message keeps the kind's relative link."""
    console = get_settings().console_url
    return f"{console.rstrip('/')}{KINDS[kind].link}" if console else None


async def reconciliation_mismatch(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """A reconciliation run opened items (M5 §3.4, once per such run): to every admin, with how
    many and of which kinds; the items themselves are on the console's Reconciliation page."""
    admins = await identity_api.users_with_role(session, "admin")
    if not admins:
        return None
    kind = "billing.reconciliation_mismatch"
    values = {"opened": payload["opened"], "kinds": payload["kinds"]}
    return Route(kind, admins, checked(kind, values), link=console_link(kind))


async def plan_needs_review(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """A route plan was left `solved` because M7's check found problems (M7 §3.2): to the hub's
    managers and every admin, naming the hub and the delivery day. A hub that no longer exists
    is logged and nobody is told, as for drift."""
    hub_id = uuid.UUID(str(payload["hub_id"]))
    managers = await identity_api.users_with_role(session, "hub_manager", hub_id)
    admins = await identity_api.users_with_role(session, "admin")
    people = list(dict.fromkeys([*managers, *admins]))  # a manager who is also an admin: once
    if not people:
        return None
    hub = await hub_ops_api.get_hub(session, hub_id)
    if hub is None:
        log.warning("plan_hub_missing", hub_id=str(hub_id))
        return None
    kind = "logistics.plan_needs_review"
    values = {"hub": hub.name, "day": payload["delivery_date"]}
    return Route(kind, people, checked(kind, values), link=console_link(kind))


async def partner_verified(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """To the person M8 just verified, in the words of their kind (M8 §10). A captain hears
    again once M9 gives her a society (`community.captain_assigned`)."""
    kind = f"partners.verified.{payload['kind']}"
    if kind not in KINDS:
        raise ValueError(f"unknown partner kind {payload['kind']!r}")
    return _to_user(kind, payload)


async def captain_assigned(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """To the captain M9 appointed (M9 §3.2); like the event, it names no society or flat."""
    return _to_user("community.captain_assigned", payload)


async def points_credited(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """Points from a referral or any later source (M5 §3.6). A donation's points are told once, by
    `community.donation_verified`, which also says what was counted (M9 §3.4)."""
    if payload["source_type"] == "donation":
        return None
    return _to_user("billing.points_credited", payload)


async def donation_verified(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """To the donor, with the points the hub awarded (M9 §3.4)."""
    return _to_user("community.donation_verified", payload)


async def society_live(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """Delivery is free now: to the owner of each household in the society (M9 §3.6). The hub
    managers' "set delivery days" console task is not a message."""
    society_id = uuid.UUID(str(payload["society_id"]))
    owners = await identity_api.society_owners(session, society_id)
    if not owners:
        return None
    return Route("community.society_live", sorted(owners), {}, link=f"/society/{society_id}")


async def assignment_created(session: AsyncSession, payload: dict[str, Any]) -> Route | None:
    """The offer to the kitchen, with what is asked and until when it can answer (M8 §3.4)."""
    kind = "partners.assignment_created"
    values = {
        "portions": payload["portions"],
        "meal": payload["meal"],
        "day": payload["date"],
        "respond_by": payload["respond_by"],
    }
    return Route(kind, [uuid.UUID(str(payload["user_id"]))], checked(kind, values))


async def route_published(session: AsyncSession, payload: dict[str, Any]) -> list[Route] | None:
    """The rider gets the route, and each captain whose society it drops at gets her bags and
    the van's time (M7 §5; the tiffin run has its own words, M7 §3.7).

    M7 sends the event again when a hub manager changes the rider, with `previous_rider_user_id`
    set when there was one: then only the new rider is told, the route is what it was for the
    captains. The first rider assigned to a route that had none looks like a first publication,
    so a captain whose identical message arrived in the last two days is not told twice. The
    rider who lost the route is told nothing: M7's `route_cancelled` is for a route that is gone.
    """
    tiffin = {"box": False, "tiffin": True}[payload["kind"]]
    stops: list[dict[str, Any]] = payload["stops"]
    day = payload["delivery_date"]
    drops = [s for s in stops if s["kind"] == "drop"]
    suffix = "_tiffin" if tiffin else ""
    routes: list[Route] = []

    rider_id = (
        None if payload["rider_user_id"] is None else uuid.UUID(str(payload["rider_user_id"]))
    )
    if rider_id is not None:
        if tiffin:
            kitchens = [s for s in stops if s["kind"] == "kitchen"]
            values: dict[str, Any] = {
                "day": day,
                "kitchens": len({s["partner_id"] for s in kitchens}),
                "societies": len({s["society_id"] for s in drops}),
                "first_pickup": min(s["eta"] for s in kitchens),
            }
        else:
            values = {"day": day, "stops": len(drops), "depart": payload["depart"]}
        kind = f"logistics.route_published.rider{suffix}"
        routes.append(Route(kind, [rider_id], checked(kind, values)))

    if payload.get("previous_rider_user_id") is not None:
        return routes or None
    kind = f"logistics.route_published.captain{suffix}"
    by_captain: dict[uuid.UUID, list[dict[str, Any]]] = {}
    for stop in drops:
        if stop["captain_user_id"] is not None:
            by_captain.setdefault(uuid.UUID(str(stop["captain_user_id"])), []).append(stop)
    for captain_id, theirs in sorted(by_captain.items()):
        if captain_id == rider_id:
            continue  # one message per (event, person): the rider's own words come first
        values = {
            "day": day,
            "bags": sum(int(s["bags"]) for s in theirs),
            "eta": min(s["eta"] for s in theirs),
        }
        routes.append(Route(kind, [captain_id], checked(kind, values), skip_repeat=True))
    return routes or None


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
    "intel.drift_detected": drift_detected,
    "billing.reconciliation_mismatch": reconciliation_mismatch,
    "logistics.plan_needs_review": plan_needs_review,
    "partners.verified": partner_verified,
    "community.captain_assigned": captain_assigned,
    "billing.points_credited": points_credited,
    "community.donation_verified": donation_verified,
    "community.society_live": society_live,
    "logistics.route_published": route_published,
    "partners.assignment_created": assignment_created,
}
