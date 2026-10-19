"""Permissions of M8 Partner Network (spec §7). A person's own applications, training and partner
records need only sign-in (the service checks "own"); hub staff act in their hub."""

from gharbasket.platform.permissions import PERMISSIONS

for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "partners:read")
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(
        role,
        "partners:suspend",
        # The review queue and decisions, and a rider or packer application created for a
        # person without a household (§3.1).
        "partner_applications:review",
        # KYC files, through 5-minute links; every call audit-logged (owner, 2026-10-09).
        "kyc_files:read",
    )
# A kitchen partner's own kitchen and capacity (§3.4); the service checks "own".
PERMISSIONS.allow("kitchen_partner", "kitchens:write", "assignments:read", "assignments:write")
for role in ("hub_manager", "admin"):
    # Kitchen inspections, and running an assignment round or assigning by hand (§3.4).
    PERMISSIONS.allow(role, "kitchens:inspect", "assignments:manage")
# The rate card (§3.6): hub staff read their hub's, only an admin changes it.
for role in ("hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "partner_rates:read")
PERMISSIONS.allow("admin", "partner_rates:write")
