"""Outbox events published by M1 Identity & Household, named `identity.<event>` (spec §5).

No event carries health tags, fasting rules or flat numbers (DPDP): consumers read what they may
through `identity.api`.
"""

HOUSEHOLD_CREATED = "identity.household_created"
HOUSEHOLD_UPDATED = "identity.household_updated"
