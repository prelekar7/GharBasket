"""Permissions of M12 Console & Analytics (spec §7): `analytics:read` for the console roles."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "analytics:read")
PERMISSIONS.allow("hub_manager", "analytics:read")
PERMISSIONS.allow("franchise_owner", "analytics:read")
