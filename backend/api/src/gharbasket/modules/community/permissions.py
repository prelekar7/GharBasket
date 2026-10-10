"""Permissions of M9 Community & Kindness (spec §7)."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "society_requests:create")
# Referrals (§3.3): a customer's own code, referrals and claim.
PERMISSIONS.allow("customer", "referrals:read", "referrals:claim")
# Captain appointments (§3.2) and the society's unlock settings (§3.6): hub managers of the
# society's hub (checked per request), admins.
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "captains:read", "captains:write", "societies:write")
