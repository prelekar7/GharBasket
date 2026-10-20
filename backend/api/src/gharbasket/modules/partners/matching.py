"""Who cooks what (spec §3.4): eligibility, the preferred cook first (ADR-0027), and M8's own
greedy fallback for when M10 does not answer. Pure functions, so tests pin the result.

A kitchen can take an order when her skills cover the order's, her rating is at least the floor
(fewer than 5 ratings count as 4.5), she is within 5 km of the society point, and she has room.
Taking an order lowers her room and raises her rolling earnings by portions x the portion rate.
"""

import math
import uuid
from dataclasses import dataclass
from typing import Any

RATING_FLOOR = 3.8
UNRATED = 4.5  # kitchens with fewer than 5 ratings (owner, 2026-10-09)
MAX_KM = 5.0
PAISE_PER_PORTION = 6_000  # the kitchen tiffin_portion rate (§3.6) until the rate card exists
EARTH_KM = 6371.0
# When no kitchen fits, the reason of the kitchen that got furthest down this list.
REASONS = ("no_skills", "below_rating_floor", "out_of_reach", "no_room")


@dataclass(frozen=True)
class Order:
    id: uuid.UUID
    lat: float
    lng: float
    portions: int
    skills: frozenset[str]
    preferred: uuid.UUID | None = None  # the booked menu's kitchen (M4 §3.5)


@dataclass
class Cook:
    id: uuid.UUID
    lat: float
    lng: float
    left: int  # capacity left for the date and meal
    skills: frozenset[str]
    rating: float
    earnings: int  # rolling 28-day tiffin earnings, paise


@dataclass(frozen=True)
class Match:
    order_id: uuid.UUID
    partner_id: uuid.UUID
    portions: int
    km: float | None


def km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def distance(cook: Cook, order: Order) -> float:
    return km(cook.lat, cook.lng, order.lat, order.lng)


def why_not(cook: Cook, order: Order) -> str | None:
    """None when the kitchen can take the order, else the first rule it breaks."""
    if not order.skills <= cook.skills:
        return "no_skills"
    if cook.rating < RATING_FLOOR:
        return "below_rating_floor"
    if distance(cook, order) > MAX_KM:
        return "out_of_reach"
    if cook.left < order.portions:
        return "no_room"
    return None


def take(cook: Cook, order: Order) -> Match:
    cook.left -= order.portions
    cook.earnings += order.portions * PAISE_PER_PORTION
    return Match(order.id, cook.id, order.portions, round(distance(cook, order), 2))


def by_size(orders: list[Order]) -> list[Order]:
    """Largest orders first, then by order id."""
    return sorted(orders, key=lambda o: (-o.portions, str(o.id)))


def preferred_first(orders: list[Order], cooks: list[Cook]) -> tuple[list[Match], list[Order]]:
    """Each order goes to its preferred kitchen while she can take it; the rest is returned."""
    by_id = {cook.id: cook for cook in cooks}
    matches, rest = [], []
    for order in by_size(orders):
        cook = by_id.get(order.preferred) if order.preferred else None
        if cook is not None and why_not(cook, order) is None:
            matches.append(take(cook, order))
        else:
            rest.append(order)
    return matches, rest


def greedy(orders: list[Order], cooks: list[Cook]) -> tuple[list[Match], dict[uuid.UUID, str]]:
    """M8's fallback: each order, largest first, to the eligible kitchen with the least
    (km, rolling earnings, partner id). Deterministic."""
    matches: list[Match] = []
    unassigned: dict[uuid.UUID, str] = {}
    for order in by_size(orders):
        fits = [cook for cook in cooks if why_not(cook, order) is None]
        if fits:
            best = min(fits, key=lambda c: (distance(c, order), c.earnings, str(c.id)))
            matches.append(take(best, order))
            continue
        reasons = {why_not(cook, order) or "no_room" for cook in cooks}
        unassigned[order.id] = max(reasons, key=REASONS.index) if reasons else "no_room"
    return matches, unassigned


def gini(values: list[int]) -> float:
    if not values or sum(values) == 0:
        return 0.0
    ordered = sorted(values)
    n, total = len(ordered), sum(ordered)
    weighted = sum((i + 1) * v for i, v in enumerate(ordered))
    return round((2 * weighted) / (n * total) - (n + 1) / n, 4)


def summary(matches: list[Match], cooks: list[Cook]) -> dict[str, Any]:
    """The run's numbers for the fairness dashboard: km per portion and the Gini of earnings
    over the kitchens used and over every eligible kitchen."""
    portions = sum(m.portions for m in matches)
    used = {m.partner_id for m in matches}
    return {
        "portions": portions,
        "kitchens_used": len(used),
        "km_per_portion": round(sum((m.km or 0) * m.portions for m in matches) / portions, 3)
        if portions
        else 0.0,
        "gini": gini([c.earnings for c in cooks if c.id in used]),
        "gini_eligible": gini([c.earnings for c in cooks]),
    }
