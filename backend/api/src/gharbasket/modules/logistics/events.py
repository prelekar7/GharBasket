"""Outbox events published by M7 Logistics, named `logistics.<event>` (spec §5). No names, phones,
flats, codes or coordinates in any payload."""

# route_id, plan_id, hub_id, delivery_date, kind, trip, vehicle_id, rider_user_id,
# previous_rider_user_id, depart, stops: [{seq, kind, society_id, slot_id, eta, bags,
# captain_user_id}] — M6's packing sequence, M11 (the rider, each captain)
ROUTE_PUBLISHED = "logistics.route_published"
# route_id, plan_id, hub_id, delivery_date, rider_user_id, reason: "superseded"
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
