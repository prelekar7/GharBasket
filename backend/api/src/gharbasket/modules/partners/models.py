"""Tables of M8 Partner Network. Every table sets `__table_args__ = {"schema": SCHEMA}`.

The partner core (spec §3.3, build order (a)): one `partners` row per verified person and kind.
Applications, KYC documents and training (§3.1, §3.2): `applications`, `kyc_documents`,
`training_modules` and `training_attempts`. Kitchens and their capacity (§3.4): `kitchens` and
`kitchen_capacity`. Tiffin assignments (§3.4): `assignment_runs`, `assignments`, their
`assignment_orders` and `assignment_events`. Ratings and the rate card follow in S6 (spec §4).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from geoalchemy2 import Geography, WKBElement, WKTElement
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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
from sqlalchemy.orm import Mapped, column_property, mapped_column

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


# A kitchen's menu skills (§3.4): what a tiffin product's skill tags may ask for.
SKILLS = ("veg", "jain", "no_onion_garlic", "diabetic", "egg", "nonveg")
MEALS = ("lunch", "dinner")
HYGIENE_GRADES = ("A", "B", "C")
ASSIGNMENT_STATUSES = (
    "offered",
    "accepted",
    "preparing",
    "ready",
    "completed",
    "declined",
    "expired",
    "cancelled",
)
# An assignment that still holds its orders and the kitchen's capacity.
LIVE = ("offered", "accepted", "preparing", "ready")
ASSIGNMENT_ORDER_STATUSES = ("assigned", "ready", "picked_up", "delivered", "failed", "released")
SOURCES = ("ml", "fallback", "manual")
DECLINE_REASONS = ("unwell", "no_ingredients", "family", "other")


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
        CheckConstraint(
            f"hygiene_grade IS NULL OR {_one_of('hygiene_grade', ('A', 'B', 'C'))}",
            name="hygiene_grade",
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
    # A kitchen's hygiene grade from her photos, given at approval (§3.1), for her kitchen (§3.4).
    hygiene_grade: Mapped[str | None] = mapped_column(String(1))
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


class Kitchen(Base):
    """A kitchen partner's kitchen (§3.4), made when she is verified. `address` and `location` are
    private (the kitchen, hub staff, the rider at pickup); `area` is on the public card."""

    __tablename__ = "kitchens"
    __table_args__ = (
        CheckConstraint(
            f"hygiene_grade IS NULL OR {_one_of('hygiene_grade', HYGIENE_GRADES)}",
            name="hygiene_grade",
        ),
        CheckConstraint(
            f"skills::text[] <@ ARRAY[{', '.join(repr(s) for s in SKILLS)}]::text[]", "skills"
        ),
        {"schema": SCHEMA},
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.partners.id"), primary_key=True
    )
    area: Mapped[str | None] = mapped_column(String(80))
    address: Mapped[str | None] = mapped_column(String(300))
    # Null until she sets it: a kitchen without a location is offered nothing.
    location: Mapped[WKBElement | WKTElement | None] = mapped_column(
        Geography("POINT", srid=4326, spatial_index=False)
    )
    fssai_reg_no: Mapped[str | None] = mapped_column(String(14))
    fssai_valid_until: Mapped[date | None] = mapped_column(Date)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(16)), server_default=text("'{}'"))
    # {"lunch": [Mon … Sun], "dinner": [...]}, 0 to 100 each; new kitchens start at 0.
    default_portions: Mapped[dict[str, list[int]]] = mapped_column(JSONB)
    hygiene_grade: Mapped[str | None] = mapped_column(String(1))
    inspected_on: Mapped[date | None] = mapped_column(Date)
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))

    lat: Mapped[float | None] = column_property(func.ST_Y(func.geometry(location)))
    lng: Mapped[float | None] = column_property(func.ST_X(func.geometry(location)))


class KitchenCapacity(Base):
    """A kitchen's own capacity for one date and meal (§3.4); only `set` overrides are stored,
    else the weekday's default applies."""

    __tablename__ = "kitchen_capacity"
    __table_args__ = (
        CheckConstraint(_one_of("meal", MEALS), name="meal"),
        CheckConstraint("max_portions BETWEEN 0 AND 100", name="max_portions"),
        {"schema": SCHEMA},
    )

    partner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.kitchens.partner_id"), primary_key=True
    )
    on_date: Mapped[date] = mapped_column("date", Date, primary_key=True)
    meal: Mapped[str] = mapped_column(String(8), primary_key=True)
    max_portions: Mapped[int] = mapped_column(SmallInteger)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssignmentRun(Base):
    """One assignment round for a hub, date and meal (§3.4): the fairness dashboard's rows."""

    __tablename__ = "assignment_runs"
    __table_args__ = (
        CheckConstraint(_one_of("meal", MEALS), name="meal"),
        CheckConstraint(_one_of("source", ("ml", "fallback")), name="source"),
        Index("ix_assignment_runs_hub_id_date_meal", "hub_id", "date", "meal"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    on_date: Mapped[date] = mapped_column("date", Date)
    meal: Mapped[str] = mapped_column(String(8))
    round: Mapped[int] = mapped_column(SmallInteger)
    source: Mapped[str] = mapped_column(String(8))
    model: Mapped[str] = mapped_column(String(40))
    # solved, partial, fallback or infeasible (M10's answer); fallback for M8's own greedy.
    status: Mapped[str] = mapped_column(String(16))
    portions: Mapped[int] = mapped_column(Integer)
    # [{"order_id", "reason"}]
    unassigned: Mapped[list[dict[str, str]]] = mapped_column(JSONB)
    # {"km_per_portion", "gini", "gini_eligible", ...}
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB)
    seconds: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Assignment(Base):
    """One offer of tiffin orders to one kitchen for a date and meal (§3.4)."""

    __tablename__ = "assignments"
    __table_args__ = (
        CheckConstraint(_one_of("meal", MEALS), name="meal"),
        CheckConstraint(_one_of("source", SOURCES), name="source"),
        CheckConstraint(_one_of("status", ASSIGNMENT_STATUSES), name="status"),
        CheckConstraint(
            f"decline_reason IS NULL OR {_one_of('decline_reason', DECLINE_REASONS)}",
            name="decline_reason",
        ),
        CheckConstraint("portions > 0", name="portions"),
        Index("ix_assignments_hub_id_date_status", "hub_id", "date", "status"),
        Index("ix_assignments_partner_id_date", "partner_id", "date"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    partner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.partners.id"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    on_date: Mapped[date] = mapped_column("date", Date)
    meal: Mapped[str] = mapped_column(String(8))
    # The earliest slot of its orders (M4's slot; no foreign key across schemas).
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    round: Mapped[int] = mapped_column(SmallInteger)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.assignment_runs.id"))
    source: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    portions: Mapped[int] = mapped_column(Integer)
    respond_by: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decline_reason: Mapped[str | None] = mapped_column(String(16))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checklist: Mapped[dict[str, bool] | None] = mapped_column(JSONB)
    late: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssignmentOrder(Base):
    """A tiffin order in an assignment. An order is in at most one assignment that has not
    released it."""

    __tablename__ = "assignment_orders"
    __table_args__ = (
        CheckConstraint(_one_of("status", ASSIGNMENT_ORDER_STATUSES), name="status"),
        CheckConstraint("portions > 0", name="portions"),
        Index(
            "uq_assignment_orders_order_id_held",
            "order_id",
            unique=True,
            postgresql_where=text("status <> 'released'"),
        ),
        {"schema": SCHEMA},
    )

    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.assignments.id"), primary_key=True
    )
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    portions: Mapped[int] = mapped_column(SmallInteger)
    # "T-07": the box label, unique per hub, date and meal; kept when the order moves on.
    short_code: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), server_default="assigned")
    km: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))


class AssignmentEvent(Base):
    """What happened to an assignment, by whom and when (offline taps carry `client_at`)."""

    __tablename__ = "assignment_events"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    assignment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.assignments.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(24))
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    client_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
