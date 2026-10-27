"""subscriptions: green-slot quotes and the saving on the cycle; hub_ops: the saving's hub
settings (M10 §3.8, M3 §3.6)

Every new column is nullable or has a server default, so existing rows need no backfill: no
cycle has a saving (0, no quote), and every hub gets the proposed defaults (50 % of the route
saving, at most 1,500 paise).

Revision ID: 9c2f7a41d6e3
Revises: 3e9a4c71b5d8
Create Date: 2026-10-28 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9c2f7a41d6e3"
down_revision: str | Sequence[str] | None = "3e9a4c71b5d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "slot_quotes",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("current_slot_id", sa.Uuid(), nullable=False),
        sa.Column("items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("prediction_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('ml', 'fallback', 'unpriced')", name=op.f("ck_slot_quotes_source")
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["subscriptions.weekly_cycles.id"],
            name=op.f("fk_slot_quotes_cycle_id_weekly_cycles"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slot_quotes")),
        schema="subscriptions",
    )
    op.create_index(
        "ix_slot_quotes_cycle_expires",
        "slot_quotes",
        ["cycle_id", "expires_at"],
        schema="subscriptions",
    )
    op.add_column(
        "weekly_cycles",
        sa.Column("slot_quote_id", sa.Uuid(), nullable=True),
        schema="subscriptions",
    )
    op.add_column(
        "weekly_cycles",
        sa.Column(
            "slot_saving_paise", sa.BigInteger(), server_default=sa.text("0"), nullable=False
        ),
        schema="subscriptions",
    )
    op.create_check_constraint(
        op.f("ck_weekly_cycles_slot_saving_paise"),
        "weekly_cycles",
        "slot_saving_paise >= 0",
        schema="subscriptions",
    )
    op.add_column(
        "hubs",
        sa.Column(
            "green_saving_share_pct",
            sa.SmallInteger(),
            server_default=sa.text("50"),
            nullable=False,
        ),
        schema="hub_ops",
    )
    op.add_column(
        "hubs",
        sa.Column(
            "green_saving_max_paise",
            sa.BigInteger(),
            server_default=sa.text("1500"),
            nullable=False,
        ),
        schema="hub_ops",
    )
    op.create_check_constraint(
        op.f("ck_hubs_green_saving_share_pct"),
        "hubs",
        "green_saving_share_pct BETWEEN 0 AND 100",
        schema="hub_ops",
    )
    op.create_check_constraint(
        op.f("ck_hubs_green_saving_max_paise"),
        "hubs",
        "green_saving_max_paise BETWEEN 0 AND 10000",
        schema="hub_ops",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_hubs_green_saving_max_paise"), "hubs", schema="hub_ops")
    op.drop_constraint(op.f("ck_hubs_green_saving_share_pct"), "hubs", schema="hub_ops")
    op.drop_column("hubs", "green_saving_max_paise", schema="hub_ops")
    op.drop_column("hubs", "green_saving_share_pct", schema="hub_ops")
    op.drop_constraint(
        op.f("ck_weekly_cycles_slot_saving_paise"), "weekly_cycles", schema="subscriptions"
    )
    op.drop_column("weekly_cycles", "slot_saving_paise", schema="subscriptions")
    op.drop_column("weekly_cycles", "slot_quote_id", schema="subscriptions")
    op.drop_index("ix_slot_quotes_cycle_expires", table_name="slot_quotes", schema="subscriptions")
    op.drop_table("slot_quotes", schema="subscriptions")
