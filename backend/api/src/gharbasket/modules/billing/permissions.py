"""Permissions of M5 Billing & Ledger (spec §7). A customer only ever reaches their own wallet and
Kindness points; a partner only her own payouts and earnings statement; a hub manager reads the
payouts of her hub, and only an admin retries one."""

from gharbasket.platform.permissions import PERMISSIONS

# The roles of M8's partner kinds (kitchen, captain, rider, packer).
PARTNER_ROLES = ("kitchen_partner", "captain", "rider", "packer")

PERMISSIONS.allow(
    "customer",
    "wallet:read",
    "wallet:write",
    "statements:read",
    "refunds:write",
    "kindness:read",
    "kindness:donate",
)
PERMISSIONS.allow("admin", "reconciliation:read", "reconciliation:resolve")
for role in PARTNER_ROLES:
    # Her own payouts and `GET /v1/statements?account=earnings` (§3.5); the endpoint checks "own".
    PERMISSIONS.allow(role, "payouts:read", "statements:read")
PERMISSIONS.allow("hub_manager", "payouts:read")  # her hub's, read-only
PERMISSIONS.allow("admin", "payouts:read", "payouts:write")
