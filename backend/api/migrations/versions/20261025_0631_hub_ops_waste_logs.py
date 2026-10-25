"""hub_ops: waste logs and surplus offers (M6 §3.5, §4)

Revision ID: 8d4f2a6c1e95
Revises: 5b8e1c3d7a94
Create Date: 2026-10-25 06:31:38.563229+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d4f2a6c1e95"
down_revision: str | Sequence[str] | None = "5b8e1c3d7a94"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "surplus_offers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("offered_to", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), server_default="open", nullable=False),
        sa.Column("taken_qty", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("previous_offer_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("settled_by", sa.Uuid(), nullable=True),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "offered_to IN ('kitchen', 'ngo')", name=op.f("ck_surplus_offers_offered_to")
        ),
        sa.CheckConstraint(
            "status IN ('open', 'taken', 'declined', 'expired')",
            name=op.f("ck_surplus_offers_status"),
        ),
        sa.CheckConstraint(
            "unit IN ('kg', 'l', 'piece', 'bunch', 'pack')", name=op.f("ck_surplus_offers_unit")
        ),
        sa.CheckConstraint(
            "qty > 0 AND (taken_qty IS NULL OR taken_qty >= 0)", name=op.f("ck_surplus_offers_qty")
        ),
        sa.ForeignKeyConstraint(
            ["hub_id"],
            ["hub_ops.hubs.id"],
            name=op.f("fk_surplus_offers_hub_id_hubs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["hub_ops.inventory_lots.id"],
            name=op.f("fk_surplus_offers_lot_id_inventory_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_offer_id"],
            ["hub_ops.surplus_offers.id"],
            name=op.f("fk_surplus_offers_previous_offer_id_surplus_offers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_surplus_offers")),
        schema="hub_ops",
    )
    op.create_index(
        "ix_surplus_offers_hub_id_status",
        "surplus_offers",
        ["hub_id", "status"],
        unique=False,
        schema="hub_ops",
    )
    op.create_table(
        "waste_logs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("kg", sa.Numeric(precision=10, scale=3), nullable=True),
        sa.Column("reason", sa.String(length=10), nullable=False),
        sa.Column("cascade_to", sa.String(length=8), nullable=False),
        sa.Column("offer_id", sa.Uuid(), nullable=True),
        sa.Column("taken_by_kind", sa.String(length=8), nullable=True),
        sa.Column("taken_by_partner_id", sa.Uuid(), nullable=True),
        sa.Column("taken_by_name", sa.String(length=80), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("logged_by", sa.Uuid(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "cascade_to IN ('kitchen', 'ngo', 'bin')", name=op.f("ck_waste_logs_cascade_to")
        ),
        sa.CheckConstraint(
            "reason IN ('spoiled', 'damaged', 'expired', 'pest', 'surplus', 'returned', 'other')",
            name=op.f("ck_waste_logs_reason"),
        ),
        sa.CheckConstraint(
            "taken_by_kind IS NULL OR taken_by_kind IN ('kitchen', 'ngo')",
            name=op.f("ck_waste_logs_taken_by_kind"),
        ),
        sa.CheckConstraint(
            "unit IN ('kg', 'l', 'piece', 'bunch', 'pack')", name=op.f("ck_waste_logs_unit")
        ),
        sa.CheckConstraint("qty > 0 AND (kg IS NULL OR kg >= 0)", name=op.f("ck_waste_logs_qty")),
        sa.ForeignKeyConstraint(
            ["hub_id"],
            ["hub_ops.hubs.id"],
            name=op.f("fk_waste_logs_hub_id_hubs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id"],
            ["hub_ops.inventory_lots.id"],
            name=op.f("fk_waste_logs_lot_id_inventory_lots"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["offer_id"],
            ["hub_ops.surplus_offers.id"],
            name=op.f("fk_waste_logs_offer_id_surplus_offers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waste_logs")),
        schema="hub_ops",
    )
    op.create_index(
        "ix_waste_logs_hub_id_at", "waste_logs", ["hub_id", "at"], unique=False, schema="hub_ops"
    )


def downgrade() -> None:
    op.drop_index("ix_waste_logs_hub_id_at", table_name="waste_logs", schema="hub_ops")
    op.drop_table("waste_logs", schema="hub_ops")
    op.drop_index("ix_surplus_offers_hub_id_status", table_name="surplus_offers", schema="hub_ops")
    op.drop_table("surplus_offers", schema="hub_ops")
