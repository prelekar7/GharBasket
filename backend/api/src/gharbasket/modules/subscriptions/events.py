"""Outbox events published by M3 Plans & Subscriptions, named `subscriptions.<event>` (spec §5)."""

# cycle_id, household_id, hub_id, delivery_date, total_paise, source
CYCLE_GENERATED = "subscriptions.cycle_generated"
# cycle_id, household_id, delivery_date, cutoff_at, total_paise (M11 sends the box-ready message)
BOX_READY = "subscriptions.box_ready"
# same payload as BOX_READY: the T-1 19:00 nudge for a box nobody will approve otherwise
CUTOFF_REMINDER = "subscriptions.cutoff_reminder"
# M4's ApprovedCycle payload (spec §3.1) + auto_approved (true on apply_auto_approve's approval)
BOX_APPROVED = "subscriptions.box_approved"
# cycle_id, household_id, delivery_date
BOX_SKIPPED = "subscriptions.box_skipped"
# subscription_id, household_id, paused_until
SUBSCRIPTION_PAUSED = "subscriptions.subscription_paused"
