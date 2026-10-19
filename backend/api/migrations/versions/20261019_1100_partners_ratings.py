"""partners: ratings of delivered tiffins, and when a household was asked to rate (M8 §3.5, §4)

Revision ID: 8d3f2a61b7c4
Revises: 5c1e8a47d2b9
Create Date: 2026-10-19 11:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d3f2a61b7c4"
down_revision: str | Sequence[str] | None = "5c1e8a47d2b9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ratings",
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("household_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("score", sa.SmallInteger(), nullable=False),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("score BETWEEN 1 AND 5", name=op.f("ck_ratings_score")),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["partners.partners.id"], name=op.f("fk_ratings_partner_id_partners")
        ),
        sa.PrimaryKeyConstraint("order_id", name=op.f("pk_ratings")),
        schema="partners",
    )
    op.create_index(
        "ix_ratings_partner_id_created_at",
        "ratings",
        ["partner_id", "created_at"],
        unique=False,
        schema="partners",
    )
    op.add_column(
        "assignment_orders",
        sa.Column("prompted_at", sa.DateTime(timezone=True), nullable=True),
        schema="partners",
    )


def downgrade() -> None:
    op.drop_column("assignment_orders", "prompted_at", schema="partners")
    op.drop_index("ix_ratings_partner_id_created_at", table_name="ratings", schema="partners")
    op.drop_table("ratings", schema="partners")
