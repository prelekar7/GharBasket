"""Permissions of M9 Community & Kindness (spec §7). Society editing arrives with the console."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "society_requests:create")
# Captain appointments (§3.2): hub managers of the society's hub (checked per request), admins.
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "captains:read", "captains:write")
