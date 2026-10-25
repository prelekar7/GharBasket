"""Permissions of M3 Plans & Subscriptions (spec §7)."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "cycles:read", "cycles:write", "subscriptions:write")
# Hub staff read their hub's cut-off monitor (§3.4); the delivery dates also go to the packers.
PERMISSIONS.allow("hub_manager", "cycles:read", "delivery_days:read", "delivery_days:write")
PERMISSIONS.allow("franchise_owner", "cycles:read", "delivery_days:read")
PERMISSIONS.allow("packer", "delivery_days:read")
PERMISSIONS.allow("admin", "cycles:read", "delivery_days:read", "delivery_days:write")
