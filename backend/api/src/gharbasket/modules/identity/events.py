"""Outbox events published by M1 Identity & Household, named `identity.<event>` (spec §5).

No event carries health tags, fasting rules or flat numbers (DPDP): consumers read what they may
through `identity.api`.
"""

HOUSEHOLD_CREATED = "identity.household_created"
HOUSEHOLD_UPDATED = "identity.household_updated"
# {export_id, user_id}: build_data_export picks the export up (spec §3.3, §6); M11 later.
DATA_EXPORT_REQUESTED = "identity.data_export_requested"
