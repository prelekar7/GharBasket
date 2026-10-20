"""logistics: deliveries — one row per attempt at an order's door (M7 §3.4, §4)

Attempt 1 opens when the order's route starts (with the household's delivery code), attempt 2 is
the one free redelivery. Status, reason, hold, proof and the refund are kept per attempt; unique
(order_id, attempt).

Revision ID: 760cc84da6ad
Revises: c930494318f5
Create Date: 2026-10-14 15:30:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "760cc84da6ad"
down_revision: str | Sequence[str] | None = "c930494318f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUSES = "scheduled on_route with_captain held delivered undelivered redelivery refunded triage"
REASONS = (
    "not_home refused wrong_address damaged missing other returned_to_hub not_confirmed left_behind"
)


def _in(column: str, values: str) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values.split())})"


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.SmallInteger(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("order_kind", sa.String(length=8), nullable=False),
        sa.Column("delivery_date", sa.Date(), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("stop_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("reason", sa.String(length=16), nullable=True),
        sa.Column("hold_until", sa.Time(), nullable=True),
        sa.Column("proof", sa.String(length=8), nullable=True),
        sa.Column("photo_key", sa.String(length=200), nullable=True),
        sa.Column("delivery_code", sa.String(length=8), nullable=False),
        sa.Column("code_attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("by_actor", sa.String(length=8), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("refund_paise", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.String(length=300), nullable=True),
        sa.Column("client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("attempt IN (1, 2)", name=op.f("ck_deliveries_attempt")),
        sa.CheckConstraint(
            "by_actor IS NULL OR by_actor IN ('captain', 'rider', 'hub', 'system')",
            name=op.f("ck_deliveries_by_actor"),
        ),
        sa.CheckConstraint(
            "proof IS NULL OR proof IN ('scan', 'otp', 'photo')", name=op.f("ck_deliveries_proof")
        ),
        sa.CheckConstraint(
            f"reason IS NULL OR {_in('reason', REASONS)}", name=op.f("ck_deliveries_reason")
        ),
        sa.CheckConstraint(_in("status", STATUSES), name=op.f("ck_deliveries_status")),
        sa.ForeignKeyConstraint(
            ["stop_id"],
            ["logistics.route_stops.id"],
            name=op.f("fk_deliveries_stop_id_route_stops"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deliveries")),
        sa.UniqueConstraint("order_id", "attempt", name=op.f("uq_deliveries_order_id_attempt")),
        schema="logistics",
    )
    op.create_index(
        "ix_deliveries_hub_date",
        "deliveries",
        ["hub_id", "delivery_date", "status"],
        unique=False,
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_index("ix_deliveries_hub_date", table_name="deliveries", schema="logistics")
    op.drop_table("deliveries", schema="logistics")
