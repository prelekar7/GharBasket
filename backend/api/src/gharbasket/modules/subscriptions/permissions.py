"""Permissions of M3 Plans & Subscriptions (spec §7)."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "cycles:read", "cycles:write", "subscriptions:write")
PERMISSIONS.allow("hub_manager", "cycles:read", "delivery_days:write")
PERMISSIONS.allow("admin", "cycles:read", "delivery_days:write")
