"""Facade of M5 Billing & Ledger: the only module other modules may import.

For M4 (spec §3.1, "For other modules"): call these inside your own transaction. Each movement is
keyed by the order (and reason), so a retried cut-off or refund never moves money twice.
"""

from gharbasket.modules.billing.events import WALLET_TOPPED_UP
from gharbasket.modules.billing.wallet import (
    InsufficientBalance,
    balance,
    debit_for_order,
    refund_to_wallet,
)

__all__ = [
    "WALLET_TOPPED_UP",
    "InsufficientBalance",
    "balance",
    "debit_for_order",
    "refund_to_wallet",
]
