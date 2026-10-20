"""catalog: kitchens' tiffin menus per date and meal, booked through M4 (M2 §4, M4 §3.5)

Revision ID: 483e9f3cf1cf
Revises: 98790006ab63
Create Date: 2026-10-14 19:51:28.046486+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "483e9f3cf1cf"
down_revision: str | Sequence[str] | None = "98790006ab63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tiffin_menus",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("meal", sa.String(length=8), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column(
            "recipe_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("price_paise", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=12), server_default="draft", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("meal IN ('lunch', 'dinner')", name=op.f("ck_tiffin_menus_meal")),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'withdrawn')", name=op.f("ck_tiffin_menus_status")
        ),
        sa.CheckConstraint("price_paise > 0", name=op.f("ck_tiffin_menus_price_paise")),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["catalog.products.id"],
            name=op.f("fk_tiffin_menus_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tiffin_menus")),
        sa.UniqueConstraint(
            "partner_id", "date", "meal", name=op.f("uq_tiffin_menus_partner_id_date_meal")
        ),
        schema="catalog",
    )
    op.create_index(
        "ix_tiffin_menus_hub_id_date_status",
        "tiffin_menus",
        ["hub_id", "date", "status"],
        unique=False,
        schema="catalog",
    )


def downgrade() -> None:
    op.drop_index("ix_tiffin_menus_hub_id_date_status", table_name="tiffin_menus", schema="catalog")
    op.drop_table("tiffin_menus", schema="catalog")
