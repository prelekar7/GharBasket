"""What each message is (spec §3.1 event map): its category, its link and the placeholders its
templates may use. A message names one of these keys; the key is the event type, or the event
type plus a variant when one event reads differently by outcome (an issue credited or rejected).

Placeholder types decide how a raw value from the event is shown: `money` (paise → ₹1,234),
`weekday` (an ISO date → "Monday"), `time` (an ISO timestamp → "9 PM", in IST).
"""

from dataclasses import dataclass, field
from typing import Literal

PlaceholderType = Literal["money", "weekday", "time"]

# The order the preferences screen lists them in (§3.1).
CATEGORIES = ("weekly_box", "delivery", "money", "kindness", "offers")
# Always in the inbox and pushed; not a preference.
ACCOUNT = "account"


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
}
