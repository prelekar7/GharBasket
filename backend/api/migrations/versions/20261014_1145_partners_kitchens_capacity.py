"""partners: kitchens and their capacity overrides per date and meal (M8 §3.4, §4)

Revision ID: af74586fec9d
Revises: ce599e5ada4c
Create Date: 2026-10-14 11:45:59.644713+00:00
"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "af74586fec9d"
down_revision: str | Sequence[str] | None = "ce599e5ada4c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kitchens",
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("area", sa.String(length=80), nullable=True),
        sa.Column("address", sa.String(length=300), nullable=True),
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
            nullable=True,
        ),
        sa.Column("fssai_reg_no", sa.String(length=14), nullable=True),
        sa.Column("fssai_valid_until", sa.Date(), nullable=True),
        sa.Column(
            "skills",
            postgresql.ARRAY(sa.String(length=16)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("default_portions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("hygiene_grade", sa.String(length=1), nullable=True),
        sa.Column("inspected_on", sa.Date(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(
            "hygiene_grade IS NULL OR hygiene_grade IN ('A', 'B', 'C')",
            name=op.f("ck_kitchens_hygiene_grade"),
        ),
        sa.CheckConstraint(
            "skills::text[] <@ ARRAY['veg', 'jain', 'no_onion_garlic', 'diabetic', 'egg', 'nonveg']::text[]",
            name=op.f("ck_kitchens_skills"),
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"], ["partners.partners.id"], name=op.f("fk_kitchens_partner_id_partners")
        ),
        sa.PrimaryKeyConstraint("partner_id", name=op.f("pk_kitchens")),
        schema="partners",
    )
    op.create_table(
        "kitchen_capacity",
        sa.Column("partner_id", sa.Uuid(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("meal", sa.String(length=8), nullable=False),
        sa.Column("max_portions", sa.SmallInteger(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("meal IN ('lunch', 'dinner')", name=op.f("ck_kitchen_capacity_meal")),
        sa.CheckConstraint(
            "max_portions BETWEEN 0 AND 100", name=op.f("ck_kitchen_capacity_max_portions")
        ),
        sa.ForeignKeyConstraint(
            ["partner_id"],
            ["partners.kitchens.partner_id"],
            name=op.f("fk_kitchen_capacity_partner_id_kitchens"),
        ),
        sa.PrimaryKeyConstraint("partner_id", "date", "meal", name=op.f("pk_kitchen_capacity")),
        schema="partners",
    )


def downgrade() -> None:
    op.drop_table("kitchen_capacity", schema="partners")
    op.drop_table("kitchens", schema="partners")
