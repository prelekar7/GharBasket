"""Permissions of M2 Catalog & Pricing (spec §7). Reading needs only sign-in."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("hub_manager", "prices:write", "catalog:admin_read")
PERMISSIONS.allow("admin", "prices:write", "catalog:admin_read")
PERMISSIONS.allow("catalog_editor", "catalog:admin_read")
