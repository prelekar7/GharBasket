"""orders: packer, rider, captain and kitchen actors; who made each move (ADR-0026 §2, M4 §4)

Revision ID: 386739a6e42e
Revises: a8e9dd07f72c
Create Date: 2026-10-09 04:23:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "386739a6e42e"
down_revision: str | Sequence[str] | None = "a8e9dd07f72c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "orders"
OLD_ACTORS = "'customer', 'system', 'hub'"
NEW_ACTORS = "'customer', 'system', 'hub', 'packer', 'rider', 'captain', 'kitchen'"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_order_events_actor"), "order_events", schema=SCHEMA)
    op.create_check_constraint(
        op.f("ck_order_events_actor"), "order_events", f"actor IN ({NEW_ACTORS})", schema=SCHEMA
    )
    op.add_column(
        "order_events", sa.Column("actor_user_id", sa.Uuid(), nullable=True), schema=SCHEMA
    )


def downgrade() -> None:
    op.drop_column("order_events", "actor_user_id", schema=SCHEMA)
    op.drop_constraint(op.f("ck_order_events_actor"), "order_events", schema=SCHEMA)
    # Before ADR-0026 the hub made every fulfilment move.
    op.execute(
        "UPDATE orders.order_events SET actor = 'hub' "
        "WHERE actor IN ('packer', 'rider', 'captain', 'kitchen')"
    )
    op.create_check_constraint(
        op.f("ck_order_events_actor"), "order_events", f"actor IN ({OLD_ACTORS})", schema=SCHEMA
    )
