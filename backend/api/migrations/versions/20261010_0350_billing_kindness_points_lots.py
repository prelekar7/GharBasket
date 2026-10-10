"""billing: Kindness points lots, the kindness_expense account and the points entry kinds
(M5 §3.6, §4.1)

Revision ID: 6414c91b9017
Revises: 058901d53044
Create Date: 2026-10-10 03:50:19.863870+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6414c91b9017"
down_revision: str | Sequence[str] | None = "058901d53044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "'topup', 'topup_bonus', 'box_debit', 'refund', 'adjustment'"
NEW_KINDS = f"{OLD_KINDS}, 'points_earned', 'points_expired', 'points_donated'"
OLD_REF_TYPES = "'topup', 'order'"
NEW_REF_TYPES = f"{OLD_REF_TYPES}, 'donation', 'referral'"


def _entry_checks(kinds: str, ref_types: str) -> None:
    for name in ("ck_journal_entries_kind", "ck_journal_entries_ref_type"):
        op.drop_constraint(op.f(name), "journal_entries", schema="billing", type_="check")
    op.create_check_constraint(
        op.f("ck_journal_entries_kind"), "journal_entries", f"kind IN ({kinds})", schema="billing"
    )
    op.create_check_constraint(
        op.f("ck_journal_entries_ref_type"),
        "journal_entries",
        f"ref_type IS NULL OR ref_type IN ({ref_types})",
        schema="billing",
    )


def upgrade() -> None:
    op.create_table(
        "points_lots",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("remaining", sa.Integer(), nullable=False),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("entry_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint("points > 0", name=op.f("ck_points_lots_points")),
        sa.CheckConstraint(
            "remaining >= 0 AND remaining <= points", name=op.f("ck_points_lots_remaining")
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["billing.journal_entries.id"],
            name=op.f("fk_points_lots_entry_id_journal_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_points_lots")),
        sa.UniqueConstraint(
            "source_type", "source_id", name=op.f("uq_points_lots_source_type_source_id")
        ),
        schema="billing",
    )
    op.create_index(
        op.f("ix_billing_points_lots_user_id"),
        "points_lots",
        ["user_id", "expires_at"],
        unique=False,
        schema="billing",
    )
    _entry_checks(NEW_KINDS, NEW_REF_TYPES)
    # Points are earned from the platform's kindness expense (spec §4.1), in their own currency.
    op.execute(
        "INSERT INTO billing.ledger_accounts (code, owner_type, currency, kind) "
        "VALUES ('kindness_expense', 'platform', 'KINDNESS_PTS', 'expense')"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM billing.ledger_accounts WHERE code = 'kindness_expense' "
        "AND owner_type = 'platform'"
    )
    _entry_checks(OLD_KINDS, OLD_REF_TYPES)
    op.drop_index(
        op.f("ix_billing_points_lots_user_id"), table_name="points_lots", schema="billing"
    )
    op.drop_table("points_lots", schema="billing")
