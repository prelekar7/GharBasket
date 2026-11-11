"""billing: the `revenue` entry kind (unearned_revenue → sales_revenue at delivery, M5 §3.9) and an
index on journal_entries (ref_type, ref_id), read for each order's unearned money

Revision ID: 5d1e9b3a7c42
Revises: 8b2e5d4c7a19
Create Date: 2026-11-11 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d1e9b3a7c42"
down_revision: str | Sequence[str] | None = "8b2e5d4c7a19"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "topup",
    "topup_bonus",
    "box_debit",
    "refund",
    "adjustment",
    "points_earned",
    "points_expired",
    "points_donated",
    "refund_to_source",
    "refund_to_source_failed",
    "unclaimed",
    "earning",
    "payout",
    "payout_reversed",
)
NEW_KINDS = ("revenue",)
TABLE = "billing.journal_entries"


def _kinds(kinds: tuple[str, ...]) -> None:
    listed = ", ".join(repr(k) for k in kinds)
    op.execute(f"ALTER TABLE {TABLE} DROP CONSTRAINT ck_journal_entries_kind")
    op.execute(
        f"ALTER TABLE {TABLE} ADD CONSTRAINT ck_journal_entries_kind CHECK (kind IN ({listed}))"
    )


def upgrade() -> None:
    _kinds(KINDS + NEW_KINDS)
    op.create_index(
        op.f("ix_billing_journal_entries_ref_type"),
        "journal_entries",
        ["ref_type", "ref_id"],
        schema="billing",
    )


def downgrade() -> None:
    # Recognised revenue and the refunds it paid for cannot be taken back here: deleting the
    # `revenue` entries would leave those refunds' sales_revenue legs below zero. Refuse instead.
    bind = op.get_bind()
    revenue = bind.execute(
        sa.text("SELECT count(*) FROM billing.journal_entries WHERE kind = 'revenue'")
    ).scalar_one()
    returns = bind.execute(
        sa.text(
            "SELECT count(*) FROM billing.postings p"
            " JOIN billing.journal_entries e ON e.id = p.entry_id"
            " JOIN billing.ledger_accounts a ON a.id = p.account_id"
            " WHERE e.kind = 'refund' AND a.code = 'sales_revenue' AND a.owner_type = 'platform'"
        )
    ).scalar_one()
    if revenue or returns:
        raise RuntimeError(
            f"cannot downgrade 5d1e9b3a7c42: {revenue} revenue entries and {returns} refunds from"
            " sales_revenue are in the ledger (M5 §3.9); the ledger is append-only, so correct"
            " them with adjustment entries first"
        )
    op.drop_index(
        op.f("ix_billing_journal_entries_ref_type"), table_name="journal_entries", schema="billing"
    )
    _kinds(KINDS)
