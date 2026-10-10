"""logistics: vehicles (M7 §3.1, §4; ADR-0026 §6)

The first table of M7: each hub's fleet, deactivated instead of deleted, with labels unique per
hub ignoring case. Hub and rider ids are plain UUIDs without a foreign key (ADR-0026 §5).

Revision ID: 46ba92e3b0c3
Revises: 232e527f6a85
Create Date: 2026-10-10 09:08:07.421019+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "46ba92e3b0c3"
down_revision: str | Sequence[str] | None = "232e527f6a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vehicles",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("registration", sa.String(length=16), nullable=True),
        sa.Column("capacity_kg", sa.SmallInteger(), nullable=False),
        sa.Column("capacity_crates", sa.SmallInteger(), nullable=False),
        sa.Column("capacity_tiffins", sa.SmallInteger(), nullable=False),
        sa.Column("default_rider_user_id", sa.Uuid(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('cargo_3w', 'two_wheeler')", name=op.f("ck_vehicles_kind")),
        sa.CheckConstraint(
            "capacity_crates BETWEEN 0 AND 100", name=op.f("ck_vehicles_capacity_crates")
        ),
        sa.CheckConstraint("capacity_kg BETWEEN 10 AND 2000", name=op.f("ck_vehicles_capacity_kg")),
        sa.CheckConstraint(
            "capacity_tiffins BETWEEN 0 AND 100", name=op.f("ck_vehicles_capacity_tiffins")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_vehicles_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicles")),
        schema="logistics",
    )
    op.create_index(
        "uq_vehicles_hub_label",
        "vehicles",
        ["hub_id", sa.literal_column("lower(label)")],
        unique=True,
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_index("uq_vehicles_hub_label", table_name="vehicles", schema="logistics")
    op.drop_table("vehicles", schema="logistics")
