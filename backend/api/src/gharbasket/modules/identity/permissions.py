"""Permissions of M1 Identity & Household (spec §7).

Households are owner-only for now: hub managers read them once row-level security arrives.
"""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "roles:grant")
PERMISSIONS.allow("customer", "households:read", "households:write")
