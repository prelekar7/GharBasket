"""Permissions of M5 Billing & Ledger (spec §7). A customer only ever reaches their own wallet and
Kindness points."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow(
    "customer", "wallet:read", "wallet:write", "statements:read", "kindness:read", "kindness:donate"
)
