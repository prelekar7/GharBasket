"""Outbox events published by M4 Orders & Checkout, named `orders.<event>` (spec §5).

Payloads carry order_id, household_id, hub_id, society_id, delivery_date, slot_id and
total_paise, plus what the row below says; `orders.locked` also carries the lines.
"""

LOCKED = "orders.locked"  # + lines [{product_id, qty, step_qty, step_unit}]
PAYMENT_FAILED = "orders.payment_failed"  # + shortfall_paise, grace_until
CANCELLED = "orders.cancelled"  # + reason: customer | payment | skipped
PACKED = "orders.packed"
DISPATCHED = "orders.dispatched"
AT_SOCIETY = "orders.at_society"
DELIVERED = "orders.delivered"
DELIVERY_FAILED = "orders.delivery_failed"
SHORTAGE_SUBSTITUTED = "orders.shortage_substituted"  # own payload: product, shortage, refund
# Issues (§3.2) carry their own payload, without notes, photo keys or names:
ISSUE_REPORTED = "orders.issue_reported"  # issue_id, order_id, household_id, hub_id, kind, ...
ISSUE_RESOLVED = "orders.issue_resolved"  # issue_id, order_id, household_id, status, credit, by
