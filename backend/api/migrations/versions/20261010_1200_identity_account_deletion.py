"""identity: account deletion (M1 §3.5): users may be `deleting`, sessions keep the OTP's
`auth_time`, and `deletions` records each request and stays as the purge's tombstone

Revision ID: 5d81c0e3a9f2
Revises: 232e527f6a85
Create Date: 2026-10-10 12:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5d81c0e3a9f2"
down_revision: str | Sequence[str] | None = "232e527f6a85"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "identity"
DUE = sa.text("cancelled_at IS NULL AND purged_at IS NULL")


def upgrade() -> None:
    # A purged user has no row, so the old `deleted` value goes (none was ever written).
    op.execute("UPDATE identity.users SET status = 'blocked' WHERE status = 'deleted'")
    op.drop_constraint(op.f("ck_users_status"), "users", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_users_status"),
        "users",
        "status IN ('active', 'blocked', 'deleting')",
        schema=SCHEMA,
    )

    # Existing sessions count as signed in with an OTP when they began.
    op.add_column("sessions", sa.Column("auth_time", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.execute("UPDATE identity.sessions SET auth_time = created_at")
    op.alter_column("sessions", "auth_time", nullable=False, schema=SCHEMA)

    op.create_table(
        "deletions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purge_on", sa.Date(), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "blocked_reasons",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_deletions")),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_deletions_due", "deletions", ["purge_on"], schema=SCHEMA, postgresql_where=DUE
    )


def downgrade() -> None:
    op.drop_index("ix_deletions_due", table_name="deletions", schema=SCHEMA, postgresql_where=DUE)
    op.drop_table("deletions", schema=SCHEMA)
    op.drop_column("sessions", "auth_time", schema=SCHEMA)
    op.execute("UPDATE identity.users SET status = 'deleted' WHERE status = 'deleting'")
    op.drop_constraint(op.f("ck_users_status"), "users", schema=SCHEMA, type_="check")
    op.create_check_constraint(
        op.f("ck_users_status"),
        "users",
        "status IN ('active', 'blocked', 'deleted')",
        schema=SCHEMA,
    )
