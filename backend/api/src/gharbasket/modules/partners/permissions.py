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
    )
