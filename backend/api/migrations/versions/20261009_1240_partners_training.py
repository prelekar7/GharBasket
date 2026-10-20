"""partners: training modules (code, version, texts per locale, answers) and attempts per user
(M8 §3.2, §4)

Revision ID: f9c6a10db14e
Revises: 931fd98ea0ff
Create Date: 2026-10-09 12:40:18.530432+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f9c6a10db14e"
down_revision: str | Sequence[str] | None = "931fd98ea0ff"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "training_attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("module_code", sa.String(length=32), nullable=False),
        sa.Column("module_version", sa.SmallInteger(), nullable=False),
        sa.Column("score", sa.Numeric(precision=4, scale=3), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint("score >= 0 AND score <= 1", name=op.f("ck_training_attempts_score")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_training_attempts")),
        schema="partners",
    )
    op.create_index(
        "ix_training_attempts_user_id_module_code_at",
        "training_attempts",
        ["user_id", "module_code", "at"],
        unique=False,
        schema="partners",
    )
    op.create_table(
        "training_modules",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False),
        sa.Column("for_kinds", postgresql.ARRAY(sa.String(length=16)), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("minutes", sa.SmallInteger(), nullable=False),
        sa.Column("texts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("video_url", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pass_score", sa.Numeric(precision=3, scale=2), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.CheckConstraint("minutes BETWEEN 1 AND 120", name=op.f("ck_training_modules_minutes")),
        sa.CheckConstraint(
            "pass_score > 0 AND pass_score <= 1", name=op.f("ck_training_modules_pass_score")
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_training_modules_version")),
        sa.PrimaryKeyConstraint("code", "version", name=op.f("pk_training_modules")),
        schema="partners",
    )


def downgrade() -> None:
    op.drop_table("training_modules", schema="partners")
    op.drop_index(
        "ix_training_attempts_user_id_module_code_at",
        table_name="training_attempts",
        schema="partners",
    )
    op.drop_table("training_attempts", schema="partners")
