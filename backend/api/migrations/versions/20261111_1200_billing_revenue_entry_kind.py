"""billing: the `revenue` entry kind (unearned_revenue → sales_revenue at delivery, M5 §3.9) and an
index on journal_entries (ref_type, ref_id), read for each order's unearned money

Revision ID: 5d1e9b3a7c42
Revises: 8b2e5d4c7a19
Create Date: 2026-11-11 12:00:00.000000+00:00
"""

from collections.abc import Sequence

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
    op.drop_index(
        op.f("ix_billing_journal_entries_ref_type"), table_name="journal_entries", schema="billing"
    )
    # The ledger is append-only (triggers refuse DELETE), so the revenue entries are removed with
    # those two triggers off, in one transaction: the money is unearned again, as before.
    for table in ("journal_entries", "postings"):
        op.execute(f"ALTER TABLE billing.{table} DISABLE TRIGGER {table}_append_only")
    op.execute(
        "DELETE FROM billing.postings WHERE entry_id IN (SELECT id FROM billing.journal_entries"
        " WHERE kind = 'revenue')"
    )
    op.execute("DELETE FROM billing.journal_entries WHERE kind = 'revenue'")
    for table in ("journal_entries", "postings"):
        op.execute(f"ALTER TABLE billing.{table} ENABLE TRIGGER {table}_append_only")
    _kinds(KINDS)
