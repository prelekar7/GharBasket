"""Permissions of M1 Identity & Household (spec §7).

Households are owner-only for now: hub managers read them once row-level security arrives.
"""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "roles:grant")
PERMISSIONS.allow("customer", "households:read", "households:write")
# Spec §3.6: the staff lookup (audit-logged), revoking a grant, and the hub's staff list.
PERMISSIONS.allow("admin", "roles:revoke", "users:read")
PERMISSIONS.allow("hub_manager", "staff:read")
PERMISSIONS.allow("franchise_owner", "staff:read")
PERMISSIONS.allow("admin", "staff:read")
