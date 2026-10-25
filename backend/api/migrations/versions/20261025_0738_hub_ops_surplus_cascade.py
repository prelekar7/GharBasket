"""hub_ops: the 12:30 run's draw-down mark and the cascade's guards (M6 §3.4, §3.5, §4)

`packing_tasks.drawn_at` marks a packed task drawn down; an offer has at most one follower, a
waste log per offer and destination is written once, and no offer is taken above its quantity.

Revision ID: c4e7a1f9d2b6
Revises: f5199f44ecf8
Create Date: 2026-10-25 07:38:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e7a1f9d2b6"
down_revision: str | Sequence[str] | None = "f5199f44ecf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "packing_tasks",
        sa.Column("drawn_at", sa.DateTime(timezone=True), nullable=True),
        schema="hub_ops",
    )
    op.create_index(
        "ix_packing_tasks_not_drawn",
        "packing_tasks",
        ["hub_id"],
        unique=False,
        schema="hub_ops",
        postgresql_where=sa.text("status = 'packed' AND drawn_at IS NULL"),
    )
    op.create_check_constraint(
        op.f("ck_surplus_offers_taken_qty"),
        "surplus_offers",
        "taken_qty IS NULL OR taken_qty <= qty",
        schema="hub_ops",
    )
    op.create_index(
        "uq_surplus_offers_previous_offer_id",
        "surplus_offers",
        ["previous_offer_id"],
        unique=True,
        schema="hub_ops",
        postgresql_where=sa.text("previous_offer_id IS NOT NULL"),
    )
    op.create_index(
        "uq_waste_logs_offer_id_cascade_to",
        "waste_logs",
        ["offer_id", "cascade_to"],
        unique=True,
        schema="hub_ops",
        postgresql_where=sa.text("offer_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_waste_logs_offer_id_cascade_to", table_name="waste_logs", schema="hub_ops")
    op.drop_index(
        "uq_surplus_offers_previous_offer_id", table_name="surplus_offers", schema="hub_ops"
    )
    op.drop_constraint(
        op.f("ck_surplus_offers_taken_qty"), "surplus_offers", schema="hub_ops", type_="check"
    )
    op.drop_index("ix_packing_tasks_not_drawn", table_name="packing_tasks", schema="hub_ops")
    op.drop_column("packing_tasks", "drawn_at", schema="hub_ops")
