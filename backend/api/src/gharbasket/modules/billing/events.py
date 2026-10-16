"""Outbox events published by M5 Billing & Ledger, named `billing.<event>`."""

# { user_id, topup_id, amount_paise, journal_entry_id }: a top-up was credited to the wallet.
WALLET_TOPPED_UP = "billing.wallet_topped_up"
# { user_id, points, source_type, source_id, expires_on }: Kindness points were credited (§3.6);
# M11 tells the user (category kindness).
POINTS_CREDITED = "billing.points_credited"
# { entry_id, user_id, meals, points, code }: points were given as donated meals (§3.6); M9 counts
# the meals towards its NGO hand-offs and the impact counters.
POINTS_DONATED = "billing.points_donated"

# { refund_id, user_id, amount_paise, processed_paise, status }: every part of a refund to source
# is final (spec §3.3); M11 tells the customer (category money, link /wallet).
REFUND_TO_SOURCE_SETTLED = "billing.refund_to_source_settled"
