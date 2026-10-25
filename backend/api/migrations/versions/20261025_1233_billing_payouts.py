"""billing: partner payouts, the payout entry kinds and the payout_clearing account (M5 §3.5, §4)

Revision ID: a3c7e5f19b20
Revises: c4e7a1f9d2b6
Create Date: 2026-10-25 12:33:15.474558+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3c7e5f19b20"
down_revision: str | Sequence[str] | None = "c4e7a1f9d2b6"
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
)
NEW_KINDS = ("payout", "payout_reversed")
REF_TYPES = ("topup", "order", "donation", "referral", "refund")
NEW_REF_TYPES = ("payout",)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def _checks(kinds: tuple[str, ...], ref_types: tuple[str, ...]) -> None:
    table = "billing.journal_entries"
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_journal_entries_kind")
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT ck_journal_entries_ref_type")
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT ck_journal_entries_kind CHECK (kind IN ({_in(kinds)}))"
    )
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT ck_journal_entries_ref_type "
        f"CHECK (ref_type IS NULL OR ref_type IN ({_in(ref_types)}))"
    )


def upgrade() -> None:
    _checks(KINDS + NEW_KINDS, REF_TYPES + NEW_REF_TYPES)
    accounts = sa.table(
        "ledger_accounts",
        sa.column("code"),
        sa.column("owner_type"),
        sa.column("currency"),
        sa.column("kind"),
        schema="billing",
    )
    # Where a weekly payout goes from partner_payable (handbook 05 §5.7.1): an asset whose
    # balance is minus what was paid out (or is on its way).
    op.bulk_insert(
        accounts,
        [
            {
                "code": "payout_clearing",
                "owner_type": "platform",
                "currency": "INR",
                "kind": "asset",
            }
        ],
    )
    # One row per (partner, week); M8's partner and hub are plain UUIDs (no foreign key across
    # schemas). `hub_id` is beside §4's columns, for a hub manager's list.
    op.create_table(
        "payouts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("week", sa.String(length=8), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("hold_reason", sa.String(length=24), nullable=True),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("provider_ref", sa.String(length=64), nullable=True),
        sa.Column("entry_id", sa.Uuid(), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'held') = (hold_reason IS NOT NULL)", name=op.f("ck_payouts_held")
        ),
        sa.CheckConstraint(
            "hold_reason IS NULL OR hold_reason IN "
            "('kyc_incomplete', 'suspended', 'no_payout_account')",
            name=op.f("ck_payouts_hold_reason"),
        ),
        sa.CheckConstraint(
            "provider IN ('simulated', 'razorpay_route')", name=op.f("ck_payouts_provider")
        ),
        sa.CheckConstraint(
            "status IN ('held', 'pending', 'processing', 'processed', 'failed')",
            name=op.f("ck_payouts_status"),
        ),
        sa.CheckConstraint(
            "status NOT IN ('pending', 'processing', 'processed') OR entry_id IS NOT NULL",
            name=op.f("ck_payouts_entry_id"),
        ),
        sa.CheckConstraint("amount_paise > 0", name=op.f("ck_payouts_amount_paise")),
        sa.CheckConstraint("period_to = period_from + 6", name=op.f("ck_payouts_period")),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["billing.journal_entries.id"],
            name=op.f("fk_payouts_entry_id_journal_entries"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payouts")),
        sa.UniqueConstraint("partner_id", "week", name=op.f("uq_payouts_partner_id_week")),
        sa.UniqueConstraint("provider_ref", name=op.f("uq_payouts_provider_ref")),
        schema="billing",
    )
    op.create_index(
        op.f("ix_billing_payouts_hub_id"),
        "payouts",
        ["hub_id", "week"],
        unique=False,
        schema="billing",
    )
    op.create_index(
        op.f("ix_billing_payouts_status"), "payouts", ["status"], unique=False, schema="billing"
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_billing_payouts_status"), table_name="payouts", schema="billing")
    op.drop_index(op.f("ix_billing_payouts_hub_id"), table_name="payouts", schema="billing")
    op.drop_table("payouts", schema="billing")
    # The ledger is append-only (triggers refuse DELETE), so the payout entries are removed with
    # those two triggers off, in one transaction: what was paid out is owed again, as before.
    for table in ("journal_entries", "postings"):
        op.execute(f"ALTER TABLE billing.{table} DISABLE TRIGGER {table}_append_only")
    op.execute(
        "DELETE FROM billing.postings WHERE entry_id IN (SELECT id FROM billing.journal_entries"
        " WHERE kind IN ('payout', 'payout_reversed'))"
    )
    op.execute("DELETE FROM billing.journal_entries WHERE kind IN ('payout', 'payout_reversed')")
    op.execute(
        "DELETE FROM billing.ledger_accounts "
        "WHERE code = 'payout_clearing' AND owner_type = 'platform'"
    )
    for table in ("journal_entries", "postings"):
        op.execute(f"ALTER TABLE billing.{table} ENABLE TRIGGER {table}_append_only")
    _checks(KINDS, REF_TYPES)
