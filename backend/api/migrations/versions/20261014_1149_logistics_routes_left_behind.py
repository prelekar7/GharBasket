"""logistics: the orders a route left behind at its start (M7 §3.3)

`routes.left_behind` keeps the orders of a route's stops that were still locked or picking when
its rider started with leave_behind; `RouteDetail` lists them and the hub's exceptions board shows
them.

Revision ID: c9d9505d91cd
Revises: 3b85ecb87754
Create Date: 2026-10-14 11:49:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d9505d91cd"
down_revision: str | Sequence[str] | None = "3b85ecb87754"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "routes",
        sa.Column("left_behind", sa.ARRAY(sa.Uuid()), server_default="{}", nullable=False),
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_column("routes", "left_behind", schema="logistics")
