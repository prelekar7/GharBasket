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
DELIVERED = "orders.delivered"
DELIVERY_FAILED = "orders.delivery_failed"
# Own payload: order_id, household_id, product_id, shortage_qty, substitute_product_id,
# refund_paise, total_paise
SHORTAGE_SUBSTITUTED = "orders.shortage_substituted"
# Own payload (§3.5): order_id, household_id, delivery_date, meal, portions; M3 marks the meal
TIFFIN_BOOKED = "orders.tiffin_booked"
# Issues (§3.2) carry their own payload, without notes, photo keys or names:
ISSUE_REPORTED = "orders.issue_reported"  # issue_id, order_id, household_id, hub_id, kind, ...
ISSUE_RESOLVED = "orders.issue_resolved"  # issue_id, order_id, household_id, status, credit, by
