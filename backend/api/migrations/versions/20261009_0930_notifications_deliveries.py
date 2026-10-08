"""notifications: deliveries, one per message and outside channel (push, WhatsApp) with its
attempts, for send_deliveries (M11 §4, §6)

Revision ID: 9b2e5d1c7a40
Revises: 7c3e91b2d5a4
Create Date: 2026-10-09 09:30:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b2e5d1c7a40"
down_revision: str | Sequence[str] | None = "7c3e91b2d5a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "deliveries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=300), nullable=True),
        sa.Column("provider_ref", sa.String(length=200), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("channel IN ('push', 'whatsapp')", name=op.f("ck_deliveries_channel")),
        sa.CheckConstraint(
            "status IN ('queued', 'held', 'sent', 'failed', 'skipped')",
            name=op.f("ck_deliveries_status"),
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["notifications.messages.id"],
            name=op.f("fk_deliveries_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deliveries")),
        sa.UniqueConstraint("message_id", "channel", name=op.f("uq_deliveries_message_id_channel")),
        schema="notifications",
    )
    op.create_index(
        "ix_deliveries_due",
        "deliveries",
        ["next_attempt_at"],
        unique=False,
        schema="notifications",
        postgresql_where=sa.text("status IN ('queued', 'held')"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deliveries_due",
        table_name="deliveries",
        schema="notifications",
        postgresql_where=sa.text("status IN ('queued', 'held')"),
    )
    op.drop_table("deliveries", schema="notifications")
