"""billing: the earning entry kind and the fulfilment_expense account (M5 §3.5, §4.1)

Revision ID: b94e7c20a5d1
Revises: 8d3f2a61b7c4
Create Date: 2026-10-19 13:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b94e7c20a5d1"
down_revision: str | Sequence[str] | None = "8d3f2a61b7c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = (
    "kind IN ('topup', 'topup_bonus', 'box_debit', 'refund', 'adjustment', 'points_earned',"
    " 'points_expired', 'points_donated', 'refund_to_source', 'refund_to_source_failed',"
    " 'unclaimed')"
)
NEW = OLD[:-1] + ", 'earning')"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_journal_entries_kind"), "journal_entries", schema="billing")
    op.create_check_constraint(
        op.f("ck_journal_entries_kind"), "journal_entries", NEW, schema="billing"
    )
    accounts = sa.table(
        "ledger_accounts",
        sa.column("code"),
        sa.column("owner_type"),
        sa.column("currency"),
        sa.column("kind"),
        schema="billing",
    )
    # What a partner's earnings are paid from (handbook 05 §5.7.1); `partner_payable:<partner>`
    # accounts are made on first use.
    op.bulk_insert(
        accounts,
        [
            {
                "code": "fulfilment_expense",
                "owner_type": "platform",
                "currency": "INR",
                "kind": "expense",
            }
        ],
    )


def downgrade() -> None:
    # The ledger is append-only (triggers refuse DELETE), so the earnings are removed with those
    # two triggers off, in one transaction, and the old check is restored once none are left.
    for table in ("journal_entries", "postings"):
        op.execute(f"ALTER TABLE billing.{table} DISABLE TRIGGER {table}_append_only")
    op.execute(
        "DELETE FROM billing.postings WHERE entry_id IN "
        "(SELECT id FROM billing.journal_entries WHERE kind = 'earning')"
    )
    op.execute("DELETE FROM billing.journal_entries WHERE kind = 'earning'")
    op.execute(
        "DELETE FROM billing.ledger_accounts WHERE code = 'fulfilment_expense' "
        "OR (code = 'partner_payable' AND owner_type = 'partner')"
    )
    for table in ("journal_entries", "postings"):
        op.execute(f"ALTER TABLE billing.{table} ENABLE TRIGGER {table}_append_only")
    op.drop_constraint(op.f("ck_journal_entries_kind"), "journal_entries", schema="billing")
    op.create_check_constraint(
        op.f("ck_journal_entries_kind"), "journal_entries", OLD, schema="billing"
    )
