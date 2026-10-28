"""logistics: tiffin routes (M7 §3.7)

Kitchen stops carry their M8 assignment and kitchen, and a stop its tiffin portions;
`logistics.stop_orders` puts each tiffin on its kitchen stop and its drop; an attempt keeps the
tiffin's short code; field events gain `collect` and plans the source `nearest`.

Additive only: new nullable columns, `portions` with a default, a new table and two wider checks,
so it runs on a database with routes and deliveries in it.

Revision ID: 2a9bfe1bd02f
Revises: 4e81b0c95a27
Create Date: 2026-10-28 16:00:00+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2a9bfe1bd02f"
down_revision: str | Sequence[str] | None = "4e81b0c95a27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIONS = "start arrive skip handover proof undelivered hold resolve complete pickup rider_mode"
SOURCES = "ml ml_fallback fallback_previous fallback_nearest"


def _in(column: str, values: str) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values.split())})"


def _checks(actions: str, sources: str) -> None:
    op.drop_constraint(op.f("ck_field_events_action"), "field_events", schema="logistics")
    op.create_check_constraint(
        op.f("ck_field_events_action"), "field_events", _in("action", actions), schema="logistics"
    )
    op.drop_constraint(op.f("ck_route_plans_source"), "route_plans", schema="logistics")
    op.create_check_constraint(
        op.f("ck_route_plans_source"),
        "route_plans",
        f"source IS NULL OR {_in('source', sources)}",
        schema="logistics",
    )


def upgrade() -> None:
    op.add_column("route_stops", sa.Column("assignment_id", sa.Uuid()), schema="logistics")
    op.add_column("route_stops", sa.Column("partner_id", sa.Uuid()), schema="logistics")
    op.add_column(
        "route_stops",
        sa.Column("portions", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        schema="logistics",
    )
    op.add_column("deliveries", sa.Column("short_code", sa.String(length=8)), schema="logistics")
    op.create_table(
        "stop_orders",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("stop_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("short_code", sa.String(length=8), nullable=False),
        sa.Column("portions", sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["stop_id"],
            ["logistics.route_stops.id"],
            name=op.f("fk_stop_orders_stop_id_route_stops"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stop_orders")),
        sa.UniqueConstraint("stop_id", "order_id", name=op.f("uq_stop_orders_stop_id_order_id")),
        schema="logistics",
    )
    op.create_index("ix_stop_orders_order_id", "stop_orders", ["order_id"], schema="logistics")
    _checks(f"{ACTIONS} collect", f"{SOURCES} nearest")


def downgrade() -> None:
    _checks(ACTIONS, SOURCES)
    op.drop_index("ix_stop_orders_order_id", table_name="stop_orders", schema="logistics")
    op.drop_table("stop_orders", schema="logistics")
    op.drop_column("deliveries", "short_code", schema="logistics")
    op.drop_column("route_stops", "portions", schema="logistics")
    op.drop_column("route_stops", "partner_id", schema="logistics")
    op.drop_column("route_stops", "assignment_id", schema="logistics")
