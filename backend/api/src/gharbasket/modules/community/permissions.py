"""Permissions of M9 Community & Kindness (spec §7). Society editing arrives with the console."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "society_requests:create")
