"""Permissions of M2 Catalog & Pricing (spec §7). Reading needs only sign-in."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("hub_manager", "prices:write", "catalog:admin_read")
PERMISSIONS.allow("admin", "prices:write", "catalog:write", "catalog:admin_read", "recipes:curate")
PERMISSIONS.allow("catalog_editor", "catalog:admin_read", "recipes:curate")
# The tiffin menu review (§3.4): the owner's decision of 2026-10-25 gives the hub manager (own hub),
# the catalog editor and the admin the decision, and all three the list.
PERMISSIONS.allow("hub_manager", "tiffin_menus:read", "tiffin_menus:review")
PERMISSIONS.allow("catalog_editor", "tiffin_menus:read", "tiffin_menus:review")
PERMISSIONS.allow("admin", "tiffin_menus:read", "tiffin_menus:review")
