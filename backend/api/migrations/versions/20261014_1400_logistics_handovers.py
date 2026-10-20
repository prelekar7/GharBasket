"""logistics: handovers and the stop's handover time (M7 §3.4, §4)

One handover per stop: who took the bags (captain, rider in rider mode, hub), the counts against
what the van carried, the proof (code, photo, rider mode) and the rider's distance from the
society, for audit.

Revision ID: c930494318f5
Revises: 942627abf5d2
Create Date: 2026-10-14 14:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c930494318f5"
down_revision: str | Sequence[str] | None = "942627abf5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "route_stops",
        sa.Column("handed_over_at", sa.DateTime(timezone=True), nullable=True),
        schema="logistics",
    )
    op.create_table(
        "handovers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("stop_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("by_actor", sa.String(length=8), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("partner_id", sa.Uuid(), nullable=True),
        sa.Column("crates_expected", sa.SmallInteger(), nullable=False),
        sa.Column("crates_counted", sa.SmallInteger(), nullable=False),
        sa.Column("bags_expected", sa.SmallInteger(), nullable=False),
        sa.Column("bags_counted", sa.SmallInteger(), nullable=False),
        sa.Column("mismatch", sa.Boolean(), nullable=False),
        sa.Column("proof", sa.String(length=12), nullable=False),
        sa.Column("photo_key", sa.String(length=200), nullable=True),
        sa.Column("distance_m", sa.Integer(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "by_actor IN ('captain', 'rider', 'hub')", name=op.f("ck_handovers_by_actor")
        ),
        sa.CheckConstraint(
            "proof IN ('code', 'photo', 'rider_mode')", name=op.f("ck_handovers_proof")
        ),
        sa.ForeignKeyConstraint(
            ["stop_id"],
            ["logistics.route_stops.id"],
            name=op.f("fk_handovers_stop_id_route_stops"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handovers")),
        sa.UniqueConstraint("stop_id", name=op.f("uq_handovers_stop_id")),
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_table("handovers", schema="logistics")
    op.drop_column("route_stops", "handed_over_at", schema="logistics")
