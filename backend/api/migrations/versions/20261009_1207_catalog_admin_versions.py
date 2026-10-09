"""catalog: versions, editors and reviews for the console's editor (M2 §3.3), and the
catalog_editor role (M1)

Every column §3.3 adds, in one migration: `version` and `updated_by` on products, recipes and price
lists (plus `updated_at` on price lists), who last reviewed a recipe's curated flags, and `source`,
`updated_by` and `updated_at` on festival days.

Revision ID: 73514b30c629
Revises: a424ab7287c6
Create Date: 2026-10-09 12:07:48.348455+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "73514b30c629"
down_revision: str | Sequence[str] | None = "a424ab7287c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "catalog"
ROLES_BEFORE = (
    "role IN ('customer', 'captain', 'kitchen_partner', 'rider', 'packer', 'hub_manager', "
    "'franchise_owner', 'ngo_coordinator', 'admin')"
)
ROLES_AFTER = ROLES_BEFORE.removesuffix(")") + ", 'catalog_editor')"


def _version(table: str) -> None:
    op.add_column(
        table,
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        schema=SCHEMA,
    )
    op.add_column(table, sa.Column("updated_by", sa.Uuid(), nullable=True), schema=SCHEMA)


def _updated_at(table: str) -> None:
    op.add_column(
        table,
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=SCHEMA,
    )


def upgrade() -> None:
    for table in ("products", "recipes", "price_lists"):
        _version(table)
    _updated_at("price_lists")
    for flag in ("fasting", "diabetic"):
        op.add_column(
            "recipes", sa.Column(f"{flag}_reviewed_by", sa.Uuid(), nullable=True), schema=SCHEMA
        )
        op.add_column(
            "recipes",
            sa.Column(f"{flag}_reviewed_at", sa.DateTime(timezone=True), nullable=True),
            schema=SCHEMA,
        )
    op.add_column(
        "festival_calendar",
        sa.Column("source", sa.String(length=16), server_default="panchang", nullable=False),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        op.f("ck_festival_calendar_source"),
        "festival_calendar",
        "source IN ('panchang', 'manual')",
        schema=SCHEMA,
    )
    op.add_column(
        "festival_calendar", sa.Column("updated_by", sa.Uuid(), nullable=True), schema=SCHEMA
    )
    _updated_at("festival_calendar")
    op.drop_constraint(op.f("ck_user_roles_role"), "user_roles", schema="identity", type_="check")
    op.create_check_constraint(
        op.f("ck_user_roles_role"), "user_roles", ROLES_AFTER, schema="identity"
    )


def downgrade() -> None:
    op.execute("DELETE FROM identity.user_roles WHERE role = 'catalog_editor'")
    op.drop_constraint(op.f("ck_user_roles_role"), "user_roles", schema="identity", type_="check")
    op.create_check_constraint(
        op.f("ck_user_roles_role"), "user_roles", ROLES_BEFORE, schema="identity"
    )
    op.drop_constraint(
        op.f("ck_festival_calendar_source"), "festival_calendar", schema=SCHEMA, type_="check"
    )
    for column in ("source", "updated_by", "updated_at"):
        op.drop_column("festival_calendar", column, schema=SCHEMA)
    for flag in ("fasting", "diabetic"):
        op.drop_column("recipes", f"{flag}_reviewed_by", schema=SCHEMA)
        op.drop_column("recipes", f"{flag}_reviewed_at", schema=SCHEMA)
    op.drop_column("price_lists", "updated_at", schema=SCHEMA)
    for table in ("products", "recipes", "price_lists"):
        op.drop_column(table, "updated_by", schema=SCHEMA)
        op.drop_column(table, "version", schema=SCHEMA)
