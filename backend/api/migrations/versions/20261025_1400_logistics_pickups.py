"""logistics: return-leg pickups (M7 §3.6)

`logistics.pickups`: what the van takes back at a drop stop, made when its route starts: the
society's open empties (one row per stop, `ref_id` null), each donation M9 scheduled for it that
day, and each bag of a box refunded on its second attempt while the captain held it. `qty` is what
was expected, `collected_qty` what the rider reported.

Revision ID: 6b2d9e4f1a37
Revises: a9390a28e336
Create Date: 2026-10-25 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b2d9e4f1a37"
down_revision: str | Sequence[str] | None = "a9390a28e336"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pickups",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("stop_id", sa.Uuid(), nullable=False),
        sa.Column("route_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=True),
        sa.Column("household_id", sa.Uuid(), nullable=True),
        sa.Column("items", sa.String(length=300), nullable=True),
        sa.Column("qty", sa.SmallInteger(), nullable=False),
        sa.Column("collected_qty", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(length=12), server_default="expected", nullable=False),
        sa.Column("by_user_id", sa.Uuid(), nullable=True),
        sa.Column("client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('empties', 'donation', 'bag')", name=op.f("ck_pickups_kind")),
        sa.CheckConstraint(
            "status IN ('expected', 'collected', 'not_ready')", name=op.f("ck_pickups_status")
        ),
        sa.CheckConstraint("(kind = 'empties') = (ref_id IS NULL)", name=op.f("ck_pickups_ref_id")),
        sa.CheckConstraint(
            "qty >= 0 AND (collected_qty IS NULL OR collected_qty >= 0)",
            name=op.f("ck_pickups_qty"),
        ),
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["logistics.routes.id"],
            name=op.f("fk_pickups_route_id_routes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stop_id"],
            ["logistics.route_stops.id"],
            name=op.f("fk_pickups_stop_id_route_stops"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pickups")),
        sa.UniqueConstraint(
            "stop_id",
            "kind",
            "ref_id",
            name=op.f("uq_pickups_stop_id_kind_ref_id"),
            postgresql_nulls_not_distinct=True,
        ),
        schema="logistics",
    )
    op.create_index(
        "ix_pickups_hub_date", "pickups", ["hub_id", "delivery_date"], schema="logistics"
    )
    op.create_index(
        "ix_pickups_society_kind_status",
        "pickups",
        ["society_id", "kind", "status"],
        schema="logistics",
    )
    op.create_index("ix_pickups_ref", "pickups", ["ref_id"], schema="logistics")


def downgrade() -> None:
    op.drop_index("ix_pickups_ref", table_name="pickups", schema="logistics")
    op.drop_index("ix_pickups_society_kind_status", table_name="pickups", schema="logistics")
    op.drop_index("ix_pickups_hub_date", table_name="pickups", schema="logistics")
    op.drop_table("pickups", schema="logistics")
