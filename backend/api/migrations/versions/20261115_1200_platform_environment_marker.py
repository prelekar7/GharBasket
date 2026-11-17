"""platform: environment, the one-row mark of which deployment a database belongs to (ADR-0036)

Empty when created. From this revision on, every `alembic upgrade` of a shared database writes
APP_ENV into it (migrations/env.py, `gharbasket.platform.environment`): `staging` from the staging
deploy, `prod` from the production one. A `prod` mark is never replaced. The script guards read it
(`gharbasket.tools.live_guard`).

Revision ID: 62fbbb57a10c
Revises: 27eeef60dbc7
Create Date: 2026-11-15 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "62fbbb57a10c"
down_revision: str | Sequence[str] | None = "27eeef60dbc7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "environment",
        sa.Column("singleton", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("name", sa.String(length=12), nullable=False),
        sa.Column(
            "marked_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("singleton", name=op.f("ck_environment_singleton")),
        sa.CheckConstraint(
            "name IN ('dev', 'test', 'staging', 'prod')", name=op.f("ck_environment_name")
        ),
        sa.PrimaryKeyConstraint("singleton", name=op.f("pk_environment")),
        schema="platform",
    )


def downgrade() -> None:
    op.drop_table("environment", schema="platform")
