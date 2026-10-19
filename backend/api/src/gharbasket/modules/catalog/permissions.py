"""Permissions of M2 Catalog & Pricing (spec §7). Reading needs only sign-in."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("hub_manager", "prices:write", "catalog:admin_read")
PERMISSIONS.allow("admin", "prices:write", "catalog:write", "catalog:admin_read", "recipes:curate")
PERMISSIONS.allow("catalog_editor", "catalog:admin_read", "recipes:curate")
