"""The order state machine (handbook 06 §6.2): which status may follow which, and who moves it.

Pure: no database here, so the table is easy to read and to property-test.
"""

from gharbasket.platform.errors import ProblemError

# from status -> {to status: who may do it}
TRANSITIONS: dict[str, dict[str, frozenset[str]]] = {
    "approved": {
        "locked": frozenset({"system"}),
        "payment_failed": frozenset({"system"}),
        "cancelled": frozenset({"customer", "system"}),
    },
    "payment_failed": {
        "locked": frozenset({"system"}),  # a top-up inside the grace period
        "cancelled": frozenset({"system", "customer"}),
    },
    "locked": {"picking": frozenset({"hub"}), "packed": frozenset({"hub"})},
    "picking": {"packed": frozenset({"hub"})},
    "packed": {"dispatched": frozenset({"hub"})},
    "dispatched": {"at_society": frozenset({"hub"})},
    "at_society": {"delivered": frozenset({"hub"}), "undelivered": frozenset({"hub"})},
    "undelivered": {
        "redelivery": frozenset({"customer", "hub"}),
        "refunded": frozenset({"customer", "system"}),
    },
    "redelivery": {"dispatched": frozenset({"hub"})},
    "cancelled": {},
    "delivered": {},
    "refunded": {},
}
FINAL = frozenset(status for status, nexts in TRANSITIONS.items() if not nexts)
# Statuses in which money has been taken for the box.
PAID = frozenset(
    {
        "locked",
        "picking",
        "packed",
        "dispatched",
        "at_society",
        "delivered",
        "undelivered",
        "redelivery",
    }
)
# The outbox event each arrival publishes (None: no event).
EVENTS: dict[str, str | None] = {
    "locked": "orders.locked",
    "payment_failed": "orders.payment_failed",
    "cancelled": "orders.cancelled",
    "picking": None,
    "packed": "orders.packed",
    "dispatched": "orders.dispatched",
    "at_society": "orders.at_society",
    "delivered": "orders.delivered",
    "undelivered": "orders.delivery_failed",
    "redelivery": None,
    "refunded": None,  # billing publishes billing.refund_issued
}


def check(from_status: str, to_status: str, actor: str) -> None:
    """Raise `orders.illegal_transition` unless `actor` may move an order from one to the other."""
    allowed = TRANSITIONS.get(from_status, {}).get(to_status)
    if allowed is None:
        raise ProblemError(
            409,
            "orders.illegal_transition",
            f"An order cannot go from {from_status} to {to_status}.",
            extra={"from": from_status, "to": to_status},
        )
    if actor not in allowed:
        raise ProblemError(
            409,
            "orders.illegal_transition",
            f"Only {', '.join(sorted(allowed))} may move an order "
            f"from {from_status} to {to_status}.",
            extra={"from": from_status, "to": to_status, "actor": actor},
        )
