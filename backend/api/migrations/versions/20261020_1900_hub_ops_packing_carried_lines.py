"""hub_ops: carried lines on packing tasks, a key of (task, product, carried) (M6 §3.5, §4)

Revision ID: 3f9a6d1e5b82
Revises: 7c2e4a90d513
Create Date: 2026-10-20 19:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3f9a6d1e5b82"
down_revision: str | Sequence[str] | None = "7c2e4a90d513"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "packing_lines",
        sa.Column("carried", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="hub_ops",
    )
    op.drop_constraint("pk_packing_lines", "packing_lines", schema="hub_ops", type_="primary")
    op.create_primary_key(
        "pk_packing_lines",
        "packing_lines",
        ["task_id", "product_id", "carried"],
        schema="hub_ops",
    )


def downgrade() -> None:
    # A carried line is a kept item riding on the box, which the older key cannot hold.
    op.execute("DELETE FROM hub_ops.packing_lines WHERE carried")
    op.drop_constraint("pk_packing_lines", "packing_lines", schema="hub_ops", type_="primary")
    op.create_primary_key(
        "pk_packing_lines", "packing_lines", ["task_id", "product_id"], schema="hub_ops"
    )
    op.drop_column("packing_lines", "carried", schema="hub_ops")
