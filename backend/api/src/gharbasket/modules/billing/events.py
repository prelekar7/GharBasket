"""Outbox events published by M5 Billing & Ledger, named `billing.<event>`."""

# { user_id, topup_id, amount_paise, journal_entry_id }: a top-up was credited to the wallet.
WALLET_TOPPED_UP = "billing.wallet_topped_up"

# { refund_id, user_id, amount_paise, processed_paise, status }: every part of a refund to source
# is final (spec §3.3); M11 tells the customer (category money, link /wallet).
REFUND_TO_SOURCE_SETTLED = "billing.refund_to_source_settled"

# { run_id, opened, kinds }: a reconciliation run opened items (spec §3.4); M11 tells admins.
RECONCILIATION_MISMATCH = "billing.reconciliation_mismatch"
