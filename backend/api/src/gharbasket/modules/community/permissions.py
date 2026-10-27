"""Permissions of M9 Community & Kindness (spec §7)."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "society_requests:create")
# Referrals (§3.3): a customer's own code, referrals and claim.
PERMISSIONS.allow("customer", "referrals:read", "referrals:claim")
# Captain appointments (§3.2) and the society's unlock settings (§3.6): hub managers of the
# society's hub (checked per request), admins.
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "captains:read", "captains:write", "societies:write")
# Kindness (§3.4): admins set the point rates; hub managers run their hub's drives (and society
# drives in it), admins any drive.
PERMISSIONS.allow("admin", "point_rates:write")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "drives:write")
PERMISSIONS.allow("customer", "donations:create", "donations:read", "donations:cancel")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "donations:review", "donations:verify")
# NGOs, surplus offers and hand-offs (§3.5). Scopes are checked per request: hub managers their
# hubs, coordinators (ngo_coordinator:<ngo id>, an M1 role grant) their NGOs, admins everything.
PERMISSIONS.allow("admin", "ngos:write")
for role in ("hub_manager", "admin", "ngo_coordinator"):
    PERMISSIONS.allow(role, "ngos:read", "ngo_offers:read", "ngo_handoffs:read")
PERMISSIONS.allow("ngo_coordinator", "ngo_offers:claim", "ngo_handoffs:confirm")
for role in ("hub_manager", "ngo_coordinator"):
    PERMISSIONS.allow(role, "ngo_handoffs:write")
# Impact (§3.7): a customer's own (`me`), and a society's for its residents (customers), its
# captain, the staff of its hub (checked per request) and admins.
for role in ("customer", "captain", "hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "impact:read")
