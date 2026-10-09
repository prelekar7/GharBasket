"""Tables of M8 Partner Network. Every table sets `__table_args__ = {"schema": SCHEMA}`.

The partner core (spec §3.3, build order (a)): one `partners` row per verified person and kind.
Applications, KYC documents and training (§3.1, §3.2): `applications`, `kyc_documents`,
`training_modules` and `training_attempts`. Kitchens, assignments, ratings and the rate card
follow in S6 (spec §4).
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "partners"

# The app's partner kinds; M1's roles are kitchen_partner, captain, rider and packer.
KINDS = ("kitchen", "captain", "rider", "packer")
STATUSES = ("active", "paused", "suspended", "left")
# What a hub manager may give as the reason (§3.3); "left" is set on account deletion only.
SUSPEND_REASONS = ("rating", "hygiene", "compliance", "conduct", "other")
REASON_CODES = (*SUSPEND_REASONS, "left")
APPLICATION_STATUSES = ("applied", "kyc_pending", "training", "verified", "rejected", "withdrawn")
# One open application per person (§3.1).
OPEN_STATUSES = ("applied", "kyc_pending", "training")
# What each kind needs (§3.1, handbook 13 §13.8), in the order the app shows them.
REQUIRED_DOCUMENTS: dict[str, tuple[str, ...]] = {
    "kitchen": ("id_proof", "pan", "bank", "fssai", "kitchen_photos"),
    "captain": ("id_proof", "pan", "bank"),
    "rider": ("id_proof", "driving_licence", "bank"),
    "packer": ("id_proof", "bank"),
}
DOC_TYPES = ("id_proof", "pan", "bank", "fssai", "driving_licence", "kitchen_photos")
# Never Aadhaar (handbook 13).
ID_KINDS = ("voter_id", "passport", "driving_licence")
CHECK_CODES = ("format_ok", "penny_drop_ok", "penny_drop_failed", "duplicate")
REQUIRED_TRAININGS: dict[str, tuple[str, ...]] = {
    "kitchen": ("app_basics", "hygiene", "kitchen_ops"),
    "captain": ("app_basics", "captain_handover"),
    "rider": ("app_basics", "rider_safety"),
    "packer": ("app_basics", "hygiene", "packing"),
}


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Partner(Base):
    """A verified person of one kind (spec §3.3). It has no society_id (ADR-0026 §4): which
    society a captain serves is M9's `community.captains`."""

    __tablename__ = "partners"
    __table_args__ = (
        UniqueConstraint("user_id", "kind"),
        CheckConstraint(_one_of("kind", KINDS), name="kind"),
        CheckConstraint(_one_of("status", STATUSES), name="status"),
        CheckConstraint(
            f"suspend_reason_code IS NULL OR {_one_of('suspend_reason_code', REASON_CODES)}",
            name="suspend_reason_code",
        ),
        CheckConstraint(
            "status <> 'suspended'"
            " OR (suspended_at IS NOT NULL AND suspend_reason_code IS NOT NULL)",
            name="suspension",
        ),
        CheckConstraint("char_length(display_name) BETWEEN 2 AND 40", name="display_name"),
        CheckConstraint("rating_count >= 0", name="rating_count"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        Index("ix_partners_hub_id_kind_status", "hub_id", "kind", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # identity.users.id; no foreign key across module schemas.
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    kind: Mapped[str] = mapped_column(String(16))
    # The application's hub; for a captain, the requested society's hub. Checked through
    # hub_ops.api (ADR-0026 §5), no foreign key.
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # partners.applications.id once applications exist (S6); null for seeded partners.
    application_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    display_name: Mapped[str] = mapped_column(String(40))
    # A partner_photo upload comes later (§11); photo_url is null until then.
    photo_key: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), server_default="active")
    # Kitchens (§3.5): the mean of the last 50 ratings, shown from 5 on.
    rating_avg: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    rating_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspend_reason_code: Mapped[str | None] = mapped_column(String(16))
    # Staff-only: never shown to the partner herself.
    suspend_note: Mapped[str | None] = mapped_column(String(300))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Application(Base):
    """A request to become a partner of one kind (§3.1). A captain's scope is the society she
    lives in and asks to serve; everyone else's is the hub."""

    __tablename__ = "applications"
    __table_args__ = (
        CheckConstraint(_one_of("kind", KINDS), name="kind"),
        CheckConstraint(_one_of("status", APPLICATION_STATUSES), name="status"),
        CheckConstraint(_one_of("scope_type", ("society", "hub")), name="scope_type"),
        CheckConstraint("char_length(display_name) BETWEEN 2 AND 40", name="display_name"),
        CheckConstraint(
            "status <> 'verified' OR (partner_id IS NOT NULL AND verified_at IS NOT NULL)",
            name="verified",
        ),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        # One open application per person (§3.1).
        Index(
            "uq_applications_user_id_open",
            "user_id",
            unique=True,
            postgresql_where=text(_one_of("status", OPEN_STATUSES)),
        ),
        # The review queue: a hub's applications by status, oldest first.
        Index("ix_applications_hub_id_status_created_at", "hub_id", "status", "created_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # identity.users.id, the society and the hub are plain UUIDs (ADR-0026 §5).
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    display_name: Mapped[str] = mapped_column(String(40))
    # The hub manager who created it for a rider or packer without a household; else null.
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[str] = mapped_column(String(16), server_default="applied")
    # When the hub manager approved the documents (then training, or verified).
    docs_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Why it was rejected or sent back (shown to the applicant).
    reason: Mapped[str | None] = mapped_column(String(300))
    partner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.partners.id"))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrainingModule(Base):
    """One version of a short training module and its quiz (§3.2). Seed data: texts per locale
    (en, hi, mr; a missing text falls back to English) and the answers, which are never sent."""

    __tablename__ = "training_modules"
    __table_args__ = (
        CheckConstraint("pass_score > 0 AND pass_score <= 1", name="pass_score"),
        CheckConstraint("minutes BETWEEN 1 AND 120", name="minutes"),
        CheckConstraint("version >= 1", name="version"),
        {"schema": SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    for_kinds: Mapped[list[str]] = mapped_column(ARRAY(String(16)))
    required: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    minutes: Mapped[int] = mapped_column(SmallInteger)
    # {"en": {"title", "summary", "questions": [{"id", "text", "options": [...]}]}, "hi": …}
    texts: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # {"en": "https://…", …}: an unlisted video per locale; null for a read-only module.
    video_url: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    # {"q1": 2, …}: the index of the right option per question. Never returned.
    answers: Mapped[dict[str, int]] = mapped_column(JSONB)
    pass_score: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class TrainingAttempt(Base):
    """One quiz attempt (§3.2), per user rather than per partner, because training starts before
    the partner exists. Her pass of a module is her first passed attempt."""

    __tablename__ = "training_attempts"
    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 1", name="score"),
        Index("ix_training_attempts_user_id_module_code_at", "user_id", "module_code", "at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    module_code: Mapped[str] = mapped_column(String(32))
    module_version: Mapped[int] = mapped_column(SmallInteger)
    score: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    passed: Mapped[bool] = mapped_column(Boolean)
    # [{"question_id", "option"}] as sent.
    answers: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KycDocument(Base):
    """One KYC document of an application (§3.1). A number is kept only encrypted (pgcrypto, with
    its key version), masked (the last 4 characters) and hashed (HMAC, for duplicate checks); the
    FSSAI number in clear. Never an Aadhaar number or image (handbook 13)."""

    __tablename__ = "kyc_documents"
    __table_args__ = (
        CheckConstraint(_one_of("doc_type", DOC_TYPES), name="doc_type"),
        CheckConstraint(f"id_kind IS NULL OR {_one_of('id_kind', ID_KINDS)}", name="id_kind"),
        CheckConstraint(_one_of("check_status", ("passed", "failed")), name="check_status"),
        CheckConstraint(_one_of("check_code", CHECK_CODES), name="check_code"),
        CheckConstraint(
            _one_of("review_status", ("pending", "accepted", "rejected")), name="review_status"
        ),
        CheckConstraint("(number_enc IS NULL) = (key_version IS NULL)", name="key_version"),
        CheckConstraint("cardinality(file_keys) <= 4", name="file_keys"),
        Index("ix_kyc_documents_application_id_doc_type", "application_id", "doc_type"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    application_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.applications.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    doc_type: Mapped[str] = mapped_column(String(24))
    id_kind: Mapped[str | None] = mapped_column(String(24))
    # pgp_sym_encrypt(number, KYC_ENCRYPTION_KEY); nothing in the API decrypts it.
    number_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    key_version: Mapped[int | None] = mapped_column(SmallInteger)
    number_masked: Mapped[str | None] = mapped_column(String(24))
    # HMAC-SHA256(KYC_HASH_PEPPER, "<type>:<number>").
    number_hash: Mapped[bytes | None] = mapped_column(LargeBinary, index=True)
    fssai_reg_no: Mapped[str | None] = mapped_column(String(14))
    # ifsc, account_holder, valid_until, vehicle_no: never a number.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    file_keys: Mapped[list[str]] = mapped_column(ARRAY(String(255)), server_default=text("'{}'"))
    check_status: Mapped[str] = mapped_column(String(16))
    check_code: Mapped[str] = mapped_column(String(24))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[str] = mapped_column(String(16), server_default="pending")
    review_reason: Mapped[str | None] = mapped_column(String(300))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The document that replaced this one; only documents without it are current.
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.kyc_documents.id")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
