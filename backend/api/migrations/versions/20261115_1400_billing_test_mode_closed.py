"""billing: the cut-over's closing entry (M5 §3.10, ADR-0037): the `test_mode_closed` entry kind,
the `cutover` ref type, the `equity` account kind and the platform account `test_funds`

Revision ID: 6c3f1a9d2e58
Revises: 34108e224d3b
Create Date: 2026-11-15 14:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c3f1a9d2e58"
down_revision: str | Sequence[str] | None = "34108e224d3b"
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
    "revenue",
)
REF_TYPES = ("topup", "order", "donation", "referral", "refund", "payout")
ACCOUNT_KINDS = ("asset", "liability", "revenue", "expense")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(repr(v) for v in values)


def _checks(
    kinds: tuple[str, ...], ref_types: tuple[str, ...], account_kinds: tuple[str, ...]
) -> None:
    for table, name, column, values in (
        ("journal_entries", "ck_journal_entries_kind", "kind", kinds),
        ("ledger_accounts", "ck_ledger_accounts_kind", "kind", account_kinds),
    ):
        op.execute(f"ALTER TABLE billing.{table} DROP CONSTRAINT {name}")
        op.execute(
            f"ALTER TABLE billing.{table} ADD CONSTRAINT {name} CHECK ({column} IN ({_in(values)}))"
        )
    op.execute("ALTER TABLE billing.journal_entries DROP CONSTRAINT ck_journal_entries_ref_type")
    op.execute(
        "ALTER TABLE billing.journal_entries ADD CONSTRAINT ck_journal_entries_ref_type "
        f"CHECK (ref_type IS NULL OR ref_type IN ({_in(ref_types)}))"
    )


def upgrade() -> None:
    _checks((*KINDS, "test_mode_closed"), (*REF_TYPES, "cutover"), (*ACCOUNT_KINDS, "equity"))
    accounts = sa.table(
        "ledger_accounts",
        sa.column("code"),
        sa.column("owner_type"),
        sa.column("currency"),
        sa.column("kind"),
        schema="billing",
    )
    # The other side of the test money the cut-over closes (handbook 05 §5.7.1).
    op.bulk_insert(
        accounts,
        [{"code": "test_funds", "owner_type": "platform", "currency": "INR", "kind": "equity"}],
    )


def downgrade() -> None:
    # The closing entry is money history: the ledger is append-only, so refuse rather than drop it.
    closed = (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM billing.journal_entries WHERE kind = 'test_mode_closed'")
        )
        .scalar_one()
    )
    if closed:
        raise RuntimeError(
            "cannot downgrade 6c3f1a9d2e58: the test money was closed (M5 §3.10); the ledger is"
            " append-only"
        )
    op.execute(
        "DELETE FROM billing.ledger_accounts WHERE code = 'test_funds' AND owner_type = 'platform'"
    )
    _checks(KINDS, REF_TYPES, ACCOUNT_KINDS)
