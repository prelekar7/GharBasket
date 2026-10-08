"""subscriptions: edit_feedback gains meal rows for the meal swap (M3 §3.2)

Revision ID: 5b0e8d3c71a4
Revises: c9ae5e2949ab
Create Date: 2026-10-08 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b0e8d3c71a4"
down_revision: str | Sequence[str] | None = "c9ae5e2949ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "subscriptions"
PRODUCT_ACTIONS = "'removed', 'added', 'qty_up', 'qty_down', 'swapped'"
MEAL_ACTIONS = "'meal_swapped', 'meal_tiffin', 'meal_no_cook', 'meal_restored'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_edit_feedback_action"), "edit_feedback", schema=SCHEMA)
    op.alter_column(
        "edit_feedback",
        "action",
        type_=sa.String(length=16),
        existing_type=sa.String(length=10),
        existing_nullable=False,
        schema=SCHEMA,
    )
    op.alter_column(
        "edit_feedback", "product_id", nullable=True, existing_type=sa.Uuid(), schema=SCHEMA
    )
    op.add_column("edit_feedback", sa.Column("meal_date", sa.Date(), nullable=True), schema=SCHEMA)
    op.add_column(
        "edit_feedback", sa.Column("slot", sa.String(length=10), nullable=True), schema=SCHEMA
    )
    op.add_column("edit_feedback", sa.Column("recipe_id", sa.Uuid(), nullable=True), schema=SCHEMA)
    op.add_column(
        "edit_feedback",
        sa.Column("substitute_recipe_id", sa.Uuid(), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_edit_feedback_action"),
        "edit_feedback",
        f"action IN ({PRODUCT_ACTIONS}, {MEAL_ACTIONS})",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_edit_feedback_target"),
        "edit_feedback",
        f"CASE WHEN action IN ({MEAL_ACTIONS}) "
        "THEN product_id IS NULL AND meal_date IS NOT NULL AND slot IS NOT NULL "
        "ELSE product_id IS NOT NULL END",
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM {SCHEMA}.edit_feedback WHERE product_id IS NULL")  # noqa: S608
    op.drop_constraint(op.f("ck_edit_feedback_target"), "edit_feedback", schema=SCHEMA)
    op.drop_constraint(op.f("ck_edit_feedback_action"), "edit_feedback", schema=SCHEMA)
    for column in ("substitute_recipe_id", "recipe_id", "slot", "meal_date"):
        op.drop_column("edit_feedback", column, schema=SCHEMA)
    op.alter_column(
        "edit_feedback", "product_id", nullable=False, existing_type=sa.Uuid(), schema=SCHEMA
    )
    op.alter_column(
        "edit_feedback",
        "action",
        type_=sa.String(length=10),
        existing_type=sa.String(length=16),
        existing_nullable=False,
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_edit_feedback_action"),
        "edit_feedback",
        f"action IN ({PRODUCT_ACTIONS})",
        schema=SCHEMA,
    )
