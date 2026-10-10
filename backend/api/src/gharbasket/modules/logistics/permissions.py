"""Permissions of M7 Logistics (spec §7). Hub staff act only in their own hub."""

from gharbasket.platform.permissions import PERMISSIONS

for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "vehicles:read")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "vehicles:write")
