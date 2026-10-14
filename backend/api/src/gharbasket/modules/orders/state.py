"""The order state machine (handbook 06 §6.2, §6.3; ADR-0026 §2, §3): which status may follow
which for each kind of order, and which kind of actor may make the move.

Pure: no database here, so the tables are easy to read and to property-test. The tables only say
which *kind* of actor may make a move; whether this packer, rider, captain or kitchen may touch
this order is checked by the calling module (M6, M7, M8) before it calls `orders.api.transition`.
"""

from gharbasket.platform.errors import ProblemError

Table = dict[str, dict[str, frozenset[str]]]  # from status -> {to status: who may do it}

_BEFORE_CUTOFF: Table = {
    "approved": {
        "locked": frozenset({"system"}),
        "payment_failed": frozenset({"system"}),
        "cancelled": frozenset({"customer", "system"}),
    },
    "payment_failed": {
        # A top-up inside the grace period (system), or `POST /v1/orders/{id}/pay` (customer).
        "locked": frozenset({"system", "customer"}),
        "cancelled": frozenset({"system", "customer"}),
    },
}
# A box or an add-on order: packed at the hub, handed to the society's captain (to the rider only
# where the society has no active captain, which M7 checks), held there, redelivered once.
_BOX: Table = {
    **_BEFORE_CUTOFF,
    "locked": {"picking": frozenset({"packer", "hub"}), "packed": frozenset({"hub"})},
    "picking": {"packed": frozenset({"packer", "hub"})},
    "packed": {"dispatched": frozenset({"rider", "hub"})},
    "dispatched": {
        "at_society": frozenset({"captain", "rider", "hub"}),
        # Bags that ride back to the hub: a skipped stop, a count mismatch, `close_routes`
        # (ADR-0027 §5); M7 then schedules the redelivery.
        "undelivered": frozenset({"rider", "system", "hub"}),
    },
    "at_society": {
        "delivered": frozenset({"captain", "rider", "hub"}),
        # `system`: the `hold_expiry` job at the end of the hold window (ADR-0026 §6)
        "undelivered": frozenset({"captain", "rider", "system", "hub"}),
    },
    "undelivered": {
        # `system`: the free redelivery of `schedule_redeliveries` (ADR-0027 §5)
        "redelivery": frozenset({"customer", "system", "hub"}),
        "refunded": frozenset({"customer", "system"}),
    },
    "redelivery": {"dispatched": frozenset({"rider", "hub"})},
    "cancelled": {},
    "delivered": {},
    "refunded": {},
}
# A tiffin: cooked by a kitchen (picking = preparing, packed = ready), picked up there by a rider
# and handed over at the door; never held at the society, never redelivered (refunded at once).
_TIFFIN: Table = {
    **_BEFORE_CUTOFF,
    "locked": {"picking": frozenset({"kitchen", "hub"})},
    "picking": {"packed": frozenset({"kitchen", "hub"})},
    "packed": {"dispatched": frozenset({"rider", "hub"})},
    "dispatched": {
        "delivered": frozenset({"rider", "captain", "hub"}),
        "undelivered": frozenset({"rider", "captain", "hub"}),
    },
    "undelivered": {"refunded": frozenset({"system"})},
    "cancelled": {},
    "delivered": {},
    "refunded": {},
}
# order kind -> its state machine
TRANSITIONS: dict[str, Table] = {"box": _BOX, "addon": _BOX, "tiffin": _TIFFIN}
FINAL = frozenset(
    status for table in TRANSITIONS.values() for status, nexts in table.items() if not nexts
)
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
_NAMES = {"box": "a box order", "addon": "an add-on order", "tiffin": "a tiffin order"}


def check(kind: str, from_status: str, to_status: str, actor: str) -> None:
    """Raise `orders.illegal_transition` unless `actor` may move an order of this kind from one
    status to the other."""
    allowed = TRANSITIONS.get(kind, {}).get(from_status, {}).get(to_status)
    name = _NAMES.get(kind, "an order")
    if allowed is None:
        raise ProblemError(
            409,
            "orders.illegal_transition",
            f"{name.capitalize()} cannot go from {from_status} to {to_status}.",
            extra={"kind": kind, "from": from_status, "to": to_status},
        )
    if actor not in allowed:
        raise ProblemError(
            409,
            "orders.illegal_transition",
            f"Only {', '.join(sorted(allowed))} may move {name} from {from_status} to {to_status}.",
            extra={"kind": kind, "from": from_status, "to": to_status, "actor": actor},
        )
