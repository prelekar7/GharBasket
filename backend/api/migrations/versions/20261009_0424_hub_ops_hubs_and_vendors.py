"""hub_ops: hubs with their service area, and vendors (M6 §3.1, §4; ADR-0026 §5)

The first tables of M6. Hub ids elsewhere (households, societies, price lists, slots, orders,
cycles, role scopes) stay plain UUIDs without a foreign key; `just seed` upserts the seed hub
(Kharghar, SEED_HUB_ID) so they point at a real row. Vendors are per hub, unique by name
(case-insensitive), with at most one default each (partial unique index).

Revision ID: 6b1d0c4e9a27
Revises: 4f1c2a7d9e30
Create Date: 2026-10-09 04:24:07.127167+00:00
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6b1d0c4e9a27"
down_revision: str | Sequence[str] | None = "4f1c2a7d9e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hubs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("address", sa.String(length=300), nullable=False),
        sa.Column(
            "location",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=False,
        ),
        sa.Column(
            "service_area",
            geoalchemy2.types.Geography(
                geometry_type="POLYGON",
                srid=4326,
                dimension=2,
                spatial_index=False,
                from_text="ST_GeogFromText",
                name="geography",
            ),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=8), server_default="active", nullable=False),
        sa.Column("timings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "procurement_buffer_pct",
            sa.SmallInteger(),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column("data_source", sa.String(length=16), server_default="real", nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_hubs_data_source")
        ),
        sa.CheckConstraint("jsonb_typeof(timings) = 'object'", name=op.f("ck_hubs_timings")),
        sa.CheckConstraint("status IN ('active', 'paused')", name=op.f("ck_hubs_status")),
        sa.CheckConstraint(
            "procurement_buffer_pct BETWEEN 0 AND 50", name=op.f("ck_hubs_procurement_buffer_pct")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_hubs_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hubs")),
        sa.UniqueConstraint("slug", name=op.f("uq_hubs_slug")),
        schema="hub_ops",
    )
    op.create_index(
        "ix_hubs_service_area",
        "hubs",
        ["service_area"],
        unique=False,
        schema="hub_ops",
        postgresql_using="gist",
    )
    op.create_table(
        "vendors",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("phone_e164", sa.String(length=16), nullable=True),
        sa.Column("whatsapp", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('apmc_trader', 'fpo', 'farmer', 'wholesaler')", name=op.f("ck_vendors_kind")
        ),
        sa.CheckConstraint("NOT is_default OR active", name=op.f("ck_vendors_default_is_active")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_vendors_version")),
        sa.ForeignKeyConstraint(
            ["hub_id"],
            ["hub_ops.hubs.id"],
            name=op.f("fk_vendors_hub_id_hubs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vendors")),
        schema="hub_ops",
    )
    op.create_index(
        "uq_vendors_hub_default",
        "vendors",
        ["hub_id"],
        unique=True,
        schema="hub_ops",
        postgresql_where=sa.text("is_default"),
    )
    op.create_index(
        "uq_vendors_hub_name",
        "vendors",
        ["hub_id", sa.literal_column("lower(name)")],
        unique=True,
        schema="hub_ops",
    )


def downgrade() -> None:
    op.drop_index("uq_vendors_hub_name", table_name="vendors", schema="hub_ops")
    op.drop_index(
        "uq_vendors_hub_default",
        table_name="vendors",
        schema="hub_ops",
        postgresql_where=sa.text("is_default"),
    )
    op.drop_table("vendors", schema="hub_ops")
    op.drop_index(
        "ix_hubs_service_area", table_name="hubs", schema="hub_ops", postgresql_using="gist"
    )
    op.drop_table("hubs", schema="hub_ops")
