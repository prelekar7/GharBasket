"""Permissions of M11 Notifications (spec §7). The inbox and preferences need only a sign-in."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "templates:write", "notifications:support")
PERMISSIONS.allow("hub_manager", "notifications:support")  # people their hub serves
