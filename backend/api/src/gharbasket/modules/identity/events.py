"""Outbox events published by M1 Identity & Household, named `identity.<event>` (spec §5).

No event carries health tags, fasting rules or flat numbers (DPDP): consumers read what they may
through `identity.api`.
"""

HOUSEHOLD_CREATED = "identity.household_created"
HOUSEHOLD_UPDATED = "identity.household_updated"
# {export_id, user_id}: build_data_export picks the export up (spec §3.3, §6); M11 later.
DATA_EXPORT_REQUESTED = "identity.data_export_requested"
# {export_id, user_id}: the file is written; M11 says so, never with the link (spec §3.3).
DATA_EXPORT_READY = "identity.data_export_ready"
# {user_id, household_id, purge_on}: the account's deletion was requested (spec §3.5, not built
# yet); other modules do their part (M9 voids open referrals).
DELETION_REQUESTED = "identity.deletion_requested"
