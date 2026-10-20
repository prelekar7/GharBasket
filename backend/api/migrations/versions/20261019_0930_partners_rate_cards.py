"""partners: the rate card per hub, kind and basis (M8 §3.6)

Revision ID: 5c1e8a47d2b9
Revises: a7c41e5d9b02
Create Date: 2026-10-19 09:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5c1e8a47d2b9"
down_revision: str | Sequence[str] | None = "a7c41e5d9b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rate_cards",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("basis", sa.String(length=24), nullable=False),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.CheckConstraint("amount_paise >= 0", name=op.f("ck_rate_cards_amount_paise")),
        sa.CheckConstraint(
            "basis IN ('tiffin_portion', 'bag_delivered', 'dispatched_order', 'packed_order')",
            name=op.f("ck_rate_cards_basis"),
        ),
        sa.CheckConstraint(
            "kind IN ('kitchen', 'captain', 'rider', 'packer')", name=op.f("ck_rate_cards_kind")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rate_cards")),
        sa.UniqueConstraint(
            "hub_id",
            "kind",
            "basis",
            "valid_from",
            name=op.f("uq_rate_cards_hub_id_kind_basis_valid_from"),
        ),
        schema="partners",
    )


def downgrade() -> None:
    op.drop_table("rate_cards", schema="partners")
