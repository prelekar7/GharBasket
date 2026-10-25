"""Permissions of M12 Console & Analytics (spec §7): `analytics:read` for the console roles;
`analytics:ai_health` (M10 §API: the AI-health view is console admin) for admins only."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "analytics:read", "analytics:ai_health")
PERMISSIONS.allow("hub_manager", "analytics:read")
PERMISSIONS.allow("franchise_owner", "analytics:read")
# Impact (§3.6): an NGO coordinator for their NGO, hub staff for their hub, admins for everything;
# what each may read is checked per request.
for role in ("ngo_coordinator", "hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "analytics:impact")
