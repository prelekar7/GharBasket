"""Permissions of M2 Catalog & Pricing (spec §7). Reading needs only sign-in."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("hub_manager", "prices:write")
PERMISSIONS.allow("admin", "prices:write")
