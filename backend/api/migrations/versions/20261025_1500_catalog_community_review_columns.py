"""catalog: the tiffin menu review (M2 §3.4); community: the society request review (M9 §3.9)

One migration for two small contracts built in one PR.

Revision ID: c4d81e6b2f95
Revises: 5b8e1d3a7c42
Create Date: 2026-10-25 15:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d81e6b2f95"
down_revision: str | Sequence[str] | None = "5b8e1d3a7c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CATALOG = "catalog"


def upgrade() -> None:
    # M2 §3.4: `draft` becomes `submitted`; `sent_back` is new.
    op.drop_constraint(op.f("ck_tiffin_menus_status"), "tiffin_menus", schema=CATALOG)
    op.execute("UPDATE catalog.tiffin_menus SET status = 'submitted' WHERE status = 'draft'")
    op.alter_column("tiffin_menus", "status", server_default="submitted", schema=CATALOG)
    op.create_check_constraint(
        op.f("ck_tiffin_menus_status"),
        "tiffin_menus",
        "status IN ('submitted', 'approved', 'sent_back', 'withdrawn')",
        schema=CATALOG,
    )
    op.add_column(
        "tiffin_menus",
        sa.Column(
            "submitted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema=CATALOG,
    )
    op.execute("UPDATE catalog.tiffin_menus SET submitted_at = created_at")
    op.add_column(
        "tiffin_menus",
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        schema=CATALOG,
    )
    op.add_column("tiffin_menus", sa.Column("decided_by", sa.Uuid(), nullable=True), schema=CATALOG)
    op.add_column(
        "tiffin_menus", sa.Column("decision_note", sa.Text(), nullable=True), schema=CATALOG
    )
    op.add_column(
        "tiffin_menus",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        schema=CATALOG,
    )
    op.create_check_constraint(
        op.f("ck_tiffin_menus_decision_note"),
        "tiffin_menus",
        "char_length(decision_note) <= 500",
        schema=CATALOG,
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_tiffin_menus_decision_note"), "tiffin_menus", schema=CATALOG)
    op.drop_constraint(op.f("ck_tiffin_menus_status"), "tiffin_menus", schema=CATALOG)
    op.execute("UPDATE catalog.tiffin_menus SET status = 'withdrawn' WHERE status = 'sent_back'")
    op.execute("UPDATE catalog.tiffin_menus SET status = 'draft' WHERE status = 'submitted'")
    op.create_check_constraint(
        op.f("ck_tiffin_menus_status"),
        "tiffin_menus",
        "status IN ('draft', 'approved', 'withdrawn')",
        schema=CATALOG,
    )
    op.alter_column("tiffin_menus", "status", server_default="draft", schema=CATALOG)
    for column in ("version", "decision_note", "decided_by", "decided_at", "submitted_at"):
        op.drop_column("tiffin_menus", column, schema=CATALOG)
