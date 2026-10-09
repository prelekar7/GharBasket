"""Outbox events published by M3 Plans & Subscriptions, named `subscriptions.<event>` (spec §5)."""

# cycle_id, household_id, hub_id, delivery_date, total_paise, source
CYCLE_GENERATED = "subscriptions.cycle_generated"
# M4's ApprovedCycle payload (spec §3.1)
BOX_APPROVED = "subscriptions.box_approved"
# cycle_id, household_id, delivery_date
BOX_SKIPPED = "subscriptions.box_skipped"
# cycle_id, household_id, delivery_date (§3.3)
BOX_UNSKIPPED = "subscriptions.box_unskipped"
# subscription_id, household_id, paused_until
SUBSCRIPTION_PAUSED = "subscriptions.subscription_paused"
