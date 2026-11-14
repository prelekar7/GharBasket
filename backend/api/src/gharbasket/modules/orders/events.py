"""Outbox events published by M4 Orders & Checkout, named `orders.<event>` (spec §5).

Payloads carry order_id, household_id, hub_id, society_id, delivery_date, slot_id and
total_paise, plus what the row below says; `orders.locked` also carries the lines. Events a status
change publishes also carry `kind`, `actor` (the kind only, never the person) and `order_event_id`
(the id of that move's own `order_events` row, for a consumer that reads who moved it and when).
"""

LOCKED = "orders.locked"  # + lines [{product_id, qty, step_qty, step_unit}]
PAYMENT_FAILED = "orders.payment_failed"  # + shortfall_paise, grace_until
CANCELLED = "orders.cancelled"  # + reason: customer | payment | skipped
PACKED = "orders.packed"
DISPATCHED = "orders.dispatched"
AT_SOCIETY = "orders.at_society"
DELIVERED = (
    "orders.delivered"  # + carried [{carry_id, from_order_id, product_id, paid_paise}] (§3.8)
)
DELIVERY_FAILED = "orders.delivery_failed"
# Own payload: order_id, household_id, product_id, shortage_qty, substitute_product_id,
# refund_paise, total_paise
SHORTAGE_SUBSTITUTED = "orders.shortage_substituted"
# Own payload (§3.5): order_id, household_id, delivery_date, meal, portions; M3 marks the meal
TIFFIN_BOOKED = "orders.tiffin_booked"
# Issues (§3.2) carry their own payload, without notes, photo keys or names:
ISSUE_REPORTED = "orders.issue_reported"  # issue_id, order_id, household_id, hub_id, kind, ...
ISSUE_RESOLVED = "orders.issue_resolved"  # issue_id, order_id, household_id, status, credit, by
# Carry-over (§3.8): the household's kept items ride on this box or add-on order. Own payload:
# order_id (the box they ride on), household_id, hub_id, lines [{carry_id, carried_from_order_id,
# product_id, steps}]; once per box. M11 may tell the household.
CARRY_OVER_ATTACHED = "orders.carry_over_attached"
# Own payload: order_id (the missed order), household_id, hub_id, lines [{carry_id, product_id,
# steps, paid_paise}], reason "carry_over_expired"; once per missed order and run. M6 releases the
# kept items, M11 may tell the household.
CARRY_OVER_EXPIRED = "orders.carry_over_expired"
# Own payload (§3.8): order_id, household_id, hub_id. A `refunded` order with nothing left open (no
# carried row pending, attached or packed): at its move to `refunded` when nothing is carried,
# else when its last carried row is delivered or refunded; once per order. M5 recognises what the
# order still holds (M5 §3.9). Not a status change, so no `kind`, `actor` or `order_event_id`.
CLOSED = "orders.closed"
