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
# {referral_id, referrer_user_id, referee_user_id, society_id, hub_id, referrer_points,
# referee_points}: for M12 (§3.3). M11 maps nothing: billing.points_credited already notifies.
REFERRAL_CONVERTED = "community.referral_converted"
