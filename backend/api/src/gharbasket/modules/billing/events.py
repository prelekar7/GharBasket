"""Outbox events published by M5 Billing & Ledger, named `billing.<event>`."""

# { user_id, topup_id, amount_paise, journal_entry_id }: a top-up was credited to the wallet.
WALLET_TOPPED_UP = "billing.wallet_topped_up"
