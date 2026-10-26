"""Permissions of M4 Orders & Checkout (spec §7)."""

from gharbasket.platform.permissions import PERMISSIONS

# Customers see their own household's orders; hub staff their hub's (X-Active-Role, hub_id).
PERMISSIONS.allow("customer", "orders:read", "orders:write")
for role in ("packer", "rider", "hub_manager", "franchise_owner", "admin"):
    PERMISSIONS.allow(role, "orders:read")
PERMISSIONS.allow("hub_manager", "slots:write")
PERMISSIONS.allow("admin", "slots:write")
# Triage of open issues (§3.2) in the M12 console, through orders.api.resolve_issue.
PERMISSIONS.allow("hub_manager", "order_issues:resolve")
PERMISSIONS.allow("admin", "order_issues:resolve")
# The order refunds list (M5 §3.8): read only, served by M4's router.
for role in ("hub_manager", "admin"):
    PERMISSIONS.allow(role, "order_refunds:read")
