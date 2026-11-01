"""subscriptions: a cycle keeps the household's size and adult equivalents when it was drafted

The pantry twin (M10 §3.3) reads past boxes; a household whose members changed since must have
those boxes' recipe need and daily-use share scaled from the household it was then. Nullable:
cycles drafted before this keep today's household (as before).

Revision ID: 5d3c8a17e2b4
Revises: 7e4b9d20c6a1
Create Date: 2026-11-01 18:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d3c8a17e2b4"
down_revision: str | Sequence[str] | None = "7e4b9d20c6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "weekly_cycles",
        sa.Column("household_size", sa.SmallInteger(), nullable=True),
        schema="subscriptions",
    )
    op.add_column(
        "weekly_cycles",
        sa.Column("adult_equivalents", sa.Float(), nullable=True),
        schema="subscriptions",
    )
    op.create_check_constraint(
        op.f("ck_weekly_cycles_household_size"),
        "weekly_cycles",
        "household_size IS NULL OR household_size > 0",
        schema="subscriptions",
    )
    op.create_check_constraint(
        op.f("ck_weekly_cycles_adult_equivalents"),
        "weekly_cycles",
        "adult_equivalents IS NULL OR adult_equivalents > 0",
        schema="subscriptions",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_weekly_cycles_adult_equivalents"), "weekly_cycles", schema="subscriptions"
    )
    op.drop_constraint(
        op.f("ck_weekly_cycles_household_size"), "weekly_cycles", schema="subscriptions"
    )
    op.drop_column("weekly_cycles", "adult_equivalents", schema="subscriptions")
    op.drop_column("weekly_cycles", "household_size", schema="subscriptions")
