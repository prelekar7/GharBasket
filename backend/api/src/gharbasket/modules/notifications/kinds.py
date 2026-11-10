"""What each message is (spec §3.1 event map): its category, its link and the placeholders its
templates may use. A message names one of these keys; the key is the event type, or the event
type plus a variant when one event reads differently by outcome (an issue credited or rejected).

Placeholder types decide how a raw value from the event is shown: `money` (paise → ₹1,234),
`weekday` (an ISO date → "Monday"), `time` (an ISO timestamp → "9 PM", in IST), `clock` (an IST
"HH:MM" → "6:30 AM"), `number` (a count or points → 1,200), `meal` (lunch or dinner → the
word in the reader's language), `percent` (a fraction → 23%), `recon_kinds` (M5's
reconciliation kinds → their short names in the reader's language, comma-separated) and `text`
(a name, as it is).
"""

from dataclasses import dataclass, field
from typing import Literal

PlaceholderType = Literal[
    "money", "weekday", "time", "clock", "number", "meal", "percent", "recon_kinds", "text"
]

# The order the preferences screen lists them in (§3.1).
CATEGORIES = ("weekly_box", "delivery", "money", "kindness", "offers")
# Always in the inbox and pushed; not a preference.
ACCOUNT = "account"
# M8's partner kinds (`partners.verified` names one); each has its own home in the app.
PARTNER_KINDS = ("kitchen", "captain", "rider", "packer")


@dataclass(frozen=True)
class Kind:
    event_type: str
    category: str
    link: str | None
    placeholders: dict[str, PlaceholderType] = field(default_factory=dict[str, PlaceholderType])
    critical: bool = False  # skips quiet hours


KINDS: dict[str, Kind] = {
    "subscriptions.box_ready": Kind(
        "subscriptions.box_ready",
        "weekly_box",
        "/box",
        {"total": "money", "cutoff_day": "weekday", "cutoff_time": "time"},
    ),
    "subscriptions.cutoff_reminder": Kind(
        "subscriptions.cutoff_reminder", "weekly_box", "/week", {"cutoff_time": "time"}
    ),
    "subscriptions.box_approved": Kind(
        "subscriptions.box_approved", "weekly_box", "/box", {"day": "weekday"}
    ),
    "orders.locked": Kind(
        "orders.locked", "money", "/orders", {"total": "money", "day": "weekday"}
    ),
    "orders.payment_failed": Kind(
        "orders.payment_failed",
        "money",
        "/wallet",
        {"shortfall": "money", "grace_time": "time"},
        critical=True,
    ),
    "orders.cancelled": Kind("orders.cancelled", "money", "/week", {"day": "weekday"}),
    "orders.dispatched": Kind("orders.dispatched", "delivery", "/orders"),
    "orders.at_society": Kind("orders.at_society", "delivery", "/orders", critical=True),
    "orders.delivered": Kind("orders.delivered", "delivery", "/orders", critical=True),
    "orders.delivery_failed": Kind("orders.delivery_failed", "delivery", "/orders", critical=True),
    "orders.shortage_substituted": Kind(
        "orders.shortage_substituted", "delivery", "/orders", {"refund": "money"}
    ),
    "orders.issue_resolved.credited": Kind(
        "orders.issue_resolved", "money", "/orders", {"credit": "money"}
    ),
    "orders.issue_resolved.rejected": Kind("orders.issue_resolved", "money", "/orders"),
    "billing.wallet_topped_up": Kind(
        "billing.wallet_topped_up", "money", "/wallet", {"amount": "money"}
    ),
    "identity.data_export_ready": Kind("identity.data_export_ready", ACCOUNT, "/profile"),
    # To the admins, not to a customer: opens the console's AI health page (M10 §6); the link is
    # absolute when CONSOLE_URL is set, so a push to a phone opens it in the browser.
    "intel.drift_detected": Kind(
        "intel.drift_detected",
        ACCOUNT,
        "/ai-health",
        {"hub": "text", "source": "text", "live": "percent", "backtest": "percent"},
    ),
    # Also to the staff, opening the console like drift (ADR-0033): a reconciliation run opened
    # items (M5 §3.4) to every admin; a route plan left for review (M7 §3.2) to the hub's
    # managers and every admin.
    "billing.reconciliation_mismatch": Kind(
        "billing.reconciliation_mismatch",
        ACCOUNT,
        "/reconciliation",
        {"opened": "number", "kinds": "recon_kinds"},
    ),
    "logistics.plan_needs_review": Kind(
        "logistics.plan_needs_review", ACCOUNT, "/routes", {"hub": "text", "day": "weekday"}
    ),
    # Partner messages are account messages: about the work itself, not a preference to turn
    # off. One text per partner kind, each opening that kind's home in the partner app.
    **{
        f"partners.verified.{kind}": Kind("partners.verified", ACCOUNT, f"/{kind}")
        for kind in PARTNER_KINDS
    },
    "community.captain_assigned": Kind("community.captain_assigned", ACCOUNT, "/captain"),
    # Kindness messages are a preference like money: they can be turned off.
    "billing.points_credited": Kind(
        "billing.points_credited", "kindness", "/kindness", {"points": "number"}
    ),
    "community.donation_verified": Kind(
        "community.donation_verified", "kindness", "/kindness", {"points": "number"}
    ),
    "community.society_live": Kind("community.society_live", "kindness", "/kindness"),
    # Partner work messages are account messages like the verified ones. A route reads differently
    # to its rider and to a captain, and for a tiffin run (M7 §3.7).
    "logistics.route_published.rider": Kind(
        "logistics.route_published",
        ACCOUNT,
        "/rider",
        {"day": "weekday", "stops": "number", "depart": "clock"},
    ),
    "logistics.route_published.rider_tiffin": Kind(
        "logistics.route_published",
        ACCOUNT,
        "/rider",
        {"day": "weekday", "kitchens": "number", "societies": "number", "first_pickup": "clock"},
    ),
    "logistics.route_published.captain": Kind(
        "logistics.route_published",
        ACCOUNT,
        "/captain",
        {"day": "weekday", "bags": "number", "eta": "clock"},
    ),
    "logistics.route_published.captain_tiffin": Kind(
        "logistics.route_published",
        ACCOUNT,
        "/captain",
        {"day": "weekday", "bags": "number", "eta": "clock"},
    ),
    # An offer lapses at `respond_by` (T-1 22:30), inside quiet hours: held until morning it
    # would arrive too late, so it skips them like the delivery-critical messages.
    "partners.assignment_created": Kind(
        "partners.assignment_created",
        ACCOUNT,
        "/kitchen",
        {"portions": "number", "meal": "meal", "day": "weekday", "respond_by": "time"},
        critical=True,
    ),
}
