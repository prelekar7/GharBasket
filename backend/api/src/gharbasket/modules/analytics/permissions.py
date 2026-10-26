"""Permissions of M12 Console & Analytics (spec §7): `analytics:read` for the console roles;
`analytics:ai_health` (M10 §API: the AI-health view is console admin) for admins only."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "analytics:read", "analytics:ai_health")
PERMISSIONS.allow("hub_manager", "analytics:read")
PERMISSIONS.allow("franchise_owner", "analytics:read")
# Unit economics, the P&L and the hub's assumptions are the franchise's business: no hub manager.
PERMISSIONS.allow("admin", "analytics:economics", "analytics:assumptions_write")
PERMISSIONS.allow("franchise_owner", "analytics:economics", "analytics:assumptions_write")
