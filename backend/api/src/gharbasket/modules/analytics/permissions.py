"""Permissions of M12 Console & Analytics (spec §7): `analytics:read` for the console roles;
`analytics:ai_health` (M10 §API: the AI-health view is console admin) for admins only."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "analytics:read", "analytics:ai_health")
PERMISSIONS.allow("hub_manager", "analytics:read")
PERMISSIONS.allow("franchise_owner", "analytics:read")
