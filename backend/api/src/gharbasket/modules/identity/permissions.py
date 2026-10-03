"""Permissions of M1 Identity & Household (spec §7). Households arrive in a later PR."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("admin", "roles:grant")
