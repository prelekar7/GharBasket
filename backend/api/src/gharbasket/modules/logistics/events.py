"""Outbox events published by M7 Logistics, named `logistics.<event>` (spec §5). No names, phones,
flats, codes or coordinates in any payload."""

# route_id, plan_id, hub_id, delivery_date, kind, trip, vehicle_id, rider_user_id,
# previous_rider_user_id, depart, stops: [{seq, kind, society_id, slot_id, eta, bags,
# captain_user_id}] — M6's packing sequence, M11 (the rider, each captain)
ROUTE_PUBLISHED = "logistics.route_published"
# route_id, plan_id, hub_id, delivery_date, rider_user_id, reason: "superseded" | "never_started"
ROUTE_CANCELLED = "logistics.route_cancelled"
# plan_id, hub_id, delivery_date, kind, problems — M11 tells the hub's managers
PLAN_NEEDS_REVIEW = "logistics.plan_needs_review"
# stop_id, route_id, hub_id, society_id, captain_user_id, at — M11 tells the captain
STOP_ARRIVED = "logistics.stop_arrived"
# route_id, hub_id, delivery_date, rider_user_id, vehicle_id, stops_done, stops_skipped, km,
# started_at, completed_at — M12
ROUTE_COMPLETED = "logistics.route_completed"
# handover_id, stop_id, route_id, hub_id, society_id, by_actor, user_id, partner_id, crates, bags,
# order_ids, mismatch — M12 (captains are paid at orders.delivered, not here)
HANDOVER_COMPLETED = "logistics.handover_completed"
# order_id, household_id, society_id, hold_until — M11 tells the family to collect from the captain
DELIVERY_HELD = "logistics.delivery_held"
# order_id, household_id, society_id, delivery_date, window — M11
REDELIVERY_SCHEDULED = "logistics.redelivery_scheduled"
# order_id, household_id, hub_id, society_id, lines: [{product_id, steps, paid_paise}] — the
# non-perishable lines of a box missed twice, neither refunded nor surplus: they ride on the
# household's next delivery (ADR-0027 §2). M4's `carry_over` keeps them in the same transaction
# (M4 §3.8); this event stays M7's record.
CARRY_OVER_PENDING = "logistics.carry_over_pending"
# stop_id, route_id, hub_id, society_id, delivery_date, rider_user_id, empties (the stop's new count
# or null), donations: [{donation_id, status}], bag_order_ids, at — what one report changed (§3.6):
# M9 records each donation's outcome, M6 unpacks each returned bag
PICKUP_COMPLETED = "logistics.pickup_completed"
