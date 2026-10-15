"""Outbox events published by M9 Community & Kindness, named `community.<event>` (spec §5).

No event carries a name, flat, phone or photo key.
"""

# {captain_id, society_id, hub_id, user_id, partner_id, since, appointed_by}: M1 grants
# captain:<society>; M11 tells the captain.
CAPTAIN_ASSIGNED = "community.captain_assigned"
# {captain_id, society_id, hub_id, user_id, partner_id, until, reason}: M1 revokes the role.
CAPTAIN_ENDED = "community.captain_ended"
# {society_id, hub_id, joined, threshold, live_at}: once per society (§3.6). M11 tells its
# households that delivery is now free; M12 marks the date.
SOCIETY_LIVE = "community.society_live"
# Kindness donations (§3.4); none carries a name, flat, phone or photo key.
# {donation_id, user_id, household_id, hub_id, society_id, pickup_on, items: [{code, unit, qty}],
# est_kg_tenths}: M7 plans the pickup on the society's stop; again when a pickup rolls.
DONATION_SCHEDULED = "community.donation_scheduled"
# {donation_id, hub_id, society_id, pickup_on, reason}: M7 drops the expected pickup.
DONATION_CANCELLED = "community.donation_cancelled"
# {donation_id, user_id, household_id, hub_id, society_id, drive_id, points}: M5 credits the
# points (credit_points(..., "donation", donation_id), M5 §3.6); M11 notifies.
DONATION_VERIFIED = "community.donation_verified"
# {donation_id, user_id, hub_id, reason}: M11 notifies.
DONATION_REJECTED = "community.donation_rejected"
