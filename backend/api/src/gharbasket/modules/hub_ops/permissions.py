"""Permissions of M6 Hub Operations (spec §7). Hub staff act only in their own hub."""

from gharbasket.platform.permissions import PERMISSIONS

for role in ("packer", "rider", "kitchen_partner", "hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "hubs:read")
PERMISSIONS.allow("admin", "hubs:write")
for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "vendors:read")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "vendors:write")
for role in ("packer", "hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "packing:read")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "packing:manage")
