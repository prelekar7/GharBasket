"""Tables of M8 Partner Network. Every table sets `__table_args__ = {"schema": SCHEMA}`.

The partner core (spec §3.3, build order (a)): one `partners` row per verified person and kind.
Applications, KYC documents, training, kitchens, assignments, ratings and the rate card follow in
S6 (spec §4).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "partners"

# The app's partner kinds; M1's roles are kitchen_partner, captain, rider and packer.
KINDS = ("kitchen", "captain", "rider", "packer")
STATUSES = ("active", "paused", "suspended", "left")
# What a hub manager may give as the reason (§3.3); "left" is set on account deletion only.
SUSPEND_REASONS = ("rating", "hygiene", "compliance", "conduct", "other")
REASON_CODES = (*SUSPEND_REASONS, "left")


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
