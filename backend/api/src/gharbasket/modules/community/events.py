"""Outbox events published by M9 Community & Kindness, named `community.<event>` (spec §5).

Captain events never carry a name, flat or phone (§3.2).
"""

# {captain_id, society_id, hub_id, user_id, partner_id, since, appointed_by}: M1 grants
# captain:<society>; M11 tells the captain.
CAPTAIN_ASSIGNED = "community.captain_assigned"
# {captain_id, society_id, hub_id, user_id, partner_id, until, reason}: M1 revokes the role.
CAPTAIN_ENDED = "community.captain_ended"
