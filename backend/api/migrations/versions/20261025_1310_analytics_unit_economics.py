"""analytics: the version of a hub's assumptions, for the ETag and If-Match (M12 §3.4)

`assumption_versions` holds the version of each hub's assumptions (a hub without a row is at
version 1, the defaults) and `assumption_changes.version` the version a change produced, so the
history groups the keys of one PUT.

Revision ID: 5b8e1d3a7c42
Revises: a3c7e5f19b20
Create Date: 2026-10-25 13:10:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b8e1d3a7c42"
down_revision: str | Sequence[str] | None = "a3c7e5f19b20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assumption_versions",
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_assumption_versions_version")),
        sa.PrimaryKeyConstraint("hub_id", name=op.f("pk_assumption_versions")),
        schema="analytics",
    )
    # Any change row from before this revision is the hub's first save (version 2); the default
    # only fills those rows and is then dropped, so a new row must name its version.
    op.add_column(
        "assumption_changes",
        sa.Column("version", sa.Integer(), server_default="2", nullable=False),
        schema="analytics",
    )
    op.alter_column("assumption_changes", "version", server_default=None, schema="analytics")


def downgrade() -> None:
    op.drop_column("assumption_changes", "version", schema="analytics")
    op.drop_table("assumption_versions", schema="analytics")
