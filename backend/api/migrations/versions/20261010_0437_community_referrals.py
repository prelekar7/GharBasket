"""community: referral codes and referrals (M9 §3.3, §4)

Revision ID: de5f860e8882
Revises: 2ca234f73a31
Create Date: 2026-10-10 04:37:59.249403+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "de5f860e8882"
down_revision: str | Sequence[str] | None = "2ca234f73a31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "referral_codes",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_referral_codes")),
        sa.UniqueConstraint("code", name=op.f("uq_referral_codes_code")),
        schema="community",
    )
    op.create_table(
        "referrals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("referrer_user_id", sa.Uuid(), nullable=False),
        sa.Column("referee_user_id", sa.Uuid(), nullable=False),
        sa.Column("referee_household_id", sa.Uuid(), nullable=False),
        sa.Column("society_id", sa.Uuid(), nullable=False),
        sa.Column("hub_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default="joined", nullable=False),
        sa.Column("joined_on", sa.Date(), nullable=False),
        sa.Column("converted_on", sa.Date(), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=True),
        sa.Column("referrer_points", sa.Integer(), nullable=False),
        sa.Column("referee_points", sa.Integer(), nullable=False),
        sa.Column("referrer_entry_id", sa.Uuid(), nullable=True),
        sa.Column("referee_entry_id", sa.Uuid(), nullable=True),
        sa.Column("void_reason", sa.String(length=10), nullable=True),
        sa.Column("data_source", sa.String(length=16), server_default="real", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'converted') = (converted_on IS NOT NULL)",
            name=op.f("ck_referrals_converted"),
        ),
        sa.CheckConstraint(
            "referrer_points >= 0 AND referee_points >= 0", name=op.f("ck_referrals_points")
        ),
        sa.CheckConstraint(
            "data_source IN ('real', 'synthetic')", name=op.f("ck_referrals_data_source")
        ),
        sa.CheckConstraint(
            "status IN ('joined', 'converted', 'void')", name=op.f("ck_referrals_status")
        ),
        sa.CheckConstraint(
            "void_reason IS NULL OR void_reason IN ('expired', 'deletion')",
            name=op.f("ck_referrals_void_reason"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_referrals")),
        sa.UniqueConstraint("referee_user_id", name=op.f("uq_referrals_referee_user_id")),
        schema="community",
    )
    op.create_index(
        "ix_referrals_referee_household_id",
        "referrals",
        ["referee_household_id"],
        unique=False,
        schema="community",
    )
    op.create_index(
        "ix_referrals_referrer_converted_on",
        "referrals",
        ["referrer_user_id", "converted_on"],
        unique=False,
        schema="community",
    )


def downgrade() -> None:
    op.drop_index("ix_referrals_referrer_converted_on", table_name="referrals", schema="community")
    op.drop_index("ix_referrals_referee_household_id", table_name="referrals", schema="community")
    op.drop_table("referrals", schema="community")
    op.drop_table("referral_codes", schema="community")
