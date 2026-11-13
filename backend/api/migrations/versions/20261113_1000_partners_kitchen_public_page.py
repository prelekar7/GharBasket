"""partners: kitchens.public_handle and public_since, the public cook page (M8 §3.5)

Expand only: two nullable columns and the handle's unique constraint. Every existing kitchen
starts without a public page; only she can turn it on (`PATCH /v1/kitchens/me`).

Revision ID: 5d2f8a1c7e46
Revises: 8e4b2d6f1a93
Create Date: 2026-11-13 10:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d2f8a1c7e46"
down_revision: str | Sequence[str] | None = "8e4b2d6f1a93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "partners"


def upgrade() -> None:
    op.add_column("kitchens", sa.Column("public_handle", sa.Text(), nullable=True), schema=SCHEMA)
    op.add_column(
        "kitchens",
        sa.Column("public_since", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_unique_constraint(
        op.f("uq_kitchens_public_handle"), "kitchens", ["public_handle"], schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_constraint(op.f("uq_kitchens_public_handle"), "kitchens", schema=SCHEMA, type_="unique")
    op.drop_column("kitchens", "public_since", schema=SCHEMA)
    op.drop_column("kitchens", "public_handle", schema=SCHEMA)
