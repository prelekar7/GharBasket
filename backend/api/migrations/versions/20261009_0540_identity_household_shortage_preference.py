"""identity: households gain shortage_preference, "substitute" (default) or "refund" (M1 §3.4)

Revision ID: a424ab7287c6
Revises: 9b2e5d1c7a40
Create Date: 2026-10-09 05:40:02.637123+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a424ab7287c6"
down_revision: str | Sequence[str] | None = "9b2e5d1c7a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"


def upgrade() -> None:
    # The server default makes every existing household "substitute", the default of §3.4.
    op.add_column(
        "households",
        sa.Column("shortage_preference", sa.Text(), server_default="substitute", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_households_shortage_preference"),
        "households",
        "shortage_preference IN ('substitute', 'refund')",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_households_shortage_preference"), "households", schema=SCHEMA)
    op.drop_column("households", "shortage_preference", schema=SCHEMA)
