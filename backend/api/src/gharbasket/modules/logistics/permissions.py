"""Permissions of M7 Logistics (spec §7). Hub staff act only in their own hub."""

from gharbasket.platform.permissions import PERMISSIONS

for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "vehicles:read")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "vehicles:write")
PERMISSIONS.allow("hub_manager", "routes:write")
# Route plans (solve, problems, publish) are for the hub's managers and owners; packers will see the
# routes (§3.3, routes:read) but not the plans.
for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "route_plans:read")
