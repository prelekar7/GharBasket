"""logistics: field events — what riders, captains and hub managers did on a route (M7 §3.3, §4)

One row per tap (start, arrive, skip, complete; the handover and door actions follow), with the
phone's `client_at` for audit; the server's time decides everything else.

Revision ID: 05c7cd455ca0
Revises: c9d9505d91cd
Create Date: 2026-10-14 12:15:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "05c7cd455ca0"
down_revision: str | Sequence[str] | None = "c9d9505d91cd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = "start arrive skip handover proof undelivered hold resolve complete pickup rider_mode"


def upgrade() -> None:
    op.create_table(
        "field_events",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("route_id", sa.Uuid(), nullable=False),
        sa.Column("stop_id", sa.Uuid(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("action", sa.String(length=12), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("client_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            f"action IN ({', '.join(repr(a) for a in ACTIONS.split())})",
            name=op.f("ck_field_events_action"),
        ),
        sa.ForeignKeyConstraint(
            ["route_id"],
            ["logistics.routes.id"],
            name=op.f("fk_field_events_route_id_routes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["stop_id"],
            ["logistics.route_stops.id"],
            name=op.f("fk_field_events_stop_id_route_stops"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_events")),
        schema="logistics",
    )
    op.create_index(
        "ix_field_events_route",
        "field_events",
        ["route_id", "at"],
        unique=False,
        schema="logistics",
    )


def downgrade() -> None:
    op.drop_index("ix_field_events_route", table_name="field_events", schema="logistics")
    op.drop_table("field_events", schema="logistics")
