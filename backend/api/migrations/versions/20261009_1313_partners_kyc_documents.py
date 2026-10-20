"""partners: KYC documents, numbers encrypted with pgcrypto, masked and hashed; FSSAI in clear
(M8 §3.1, §4)

Revision ID: afbb215623de
Revises: f9c6a10db14e
Create Date: 2026-10-09 13:13:01.583737+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "afbb215623de"
down_revision: str | Sequence[str] | None = "f9c6a10db14e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "kyc_documents",
        sa.Column("id", sa.Uuid(), server_default=sa.text("uuidv7()"), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("doc_type", sa.String(length=24), nullable=False),
        sa.Column("id_kind", sa.String(length=24), nullable=True),
        sa.Column("number_enc", sa.LargeBinary(), nullable=True),
        sa.Column("key_version", sa.SmallInteger(), nullable=True),
        sa.Column("number_masked", sa.String(length=24), nullable=True),
        sa.Column("number_hash", sa.LargeBinary(), nullable=True),
        sa.Column("fssai_reg_no", sa.String(length=14), nullable=True),
        sa.Column(
            "details",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "file_keys",
            postgresql.ARRAY(sa.String(length=255)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("check_status", sa.String(length=16), nullable=False),
        sa.Column("check_code", sa.String(length=24), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("review_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("review_reason", sa.String(length=300), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "check_code IN ('format_ok', 'penny_drop_ok', 'penny_drop_failed', 'duplicate')",
            name=op.f("ck_kyc_documents_check_code"),
        ),
        sa.CheckConstraint(
            "check_status IN ('passed', 'failed')", name=op.f("ck_kyc_documents_check_status")
        ),
        sa.CheckConstraint(
            "doc_type IN ('id_proof', 'pan', 'bank', 'fssai', 'driving_licence', 'kitchen_photos')",
            name=op.f("ck_kyc_documents_doc_type"),
        ),
        sa.CheckConstraint(
            "id_kind IS NULL OR id_kind IN ('voter_id', 'passport', 'driving_licence')",
            name=op.f("ck_kyc_documents_id_kind"),
        ),
        sa.CheckConstraint(
            "review_status IN ('pending', 'accepted', 'rejected')",
            name=op.f("ck_kyc_documents_review_status"),
        ),
        sa.CheckConstraint(
            "(number_enc IS NULL) = (key_version IS NULL)",
            name=op.f("ck_kyc_documents_key_version"),
        ),
        sa.CheckConstraint("cardinality(file_keys) <= 4", name=op.f("ck_kyc_documents_file_keys")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["partners.applications.id"],
            name=op.f("fk_kyc_documents_application_id_applications"),
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["partners.kyc_documents.id"],
            name=op.f("fk_kyc_documents_superseded_by_kyc_documents"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_kyc_documents")),
        schema="partners",
    )
    op.create_index(
        "ix_kyc_documents_application_id_doc_type",
        "kyc_documents",
        ["application_id", "doc_type"],
        unique=False,
        schema="partners",
    )
    op.create_index(
        op.f("ix_partners_kyc_documents_number_hash"),
        "kyc_documents",
        ["number_hash"],
        unique=False,
        schema="partners",
    )
    op.create_index(
        op.f("ix_partners_kyc_documents_user_id"),
        "kyc_documents",
        ["user_id"],
        unique=False,
        schema="partners",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_partners_kyc_documents_user_id"), table_name="kyc_documents", schema="partners"
    )
    op.drop_index(
        op.f("ix_partners_kyc_documents_number_hash"), table_name="kyc_documents", schema="partners"
    )
    op.drop_index(
        "ix_kyc_documents_application_id_doc_type", table_name="kyc_documents", schema="partners"
    )
    op.drop_table("kyc_documents", schema="partners")
