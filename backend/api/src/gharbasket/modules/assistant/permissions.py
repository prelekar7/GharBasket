"""Permissions of M13 Assistant (spec §7): customers only, for their own household."""

from gharbasket.platform.permissions import PERMISSIONS

PERMISSIONS.allow("customer", "assistant:use")
