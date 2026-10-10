"""Permissions of M8 Partner Network (spec §7): the partner core's. A partner's own records need
only sign-in; the rest arrive with S6."""

from gharbasket.platform.permissions import PERMISSIONS

for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "partners:read")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "partners:suspend")
