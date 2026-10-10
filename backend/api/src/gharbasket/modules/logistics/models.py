"""Tables of M7 Logistics. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Vehicles (spec §3.1): each hub's fleet, deactivated instead of deleted. Foreign keys stay inside
`logistics`; hub and user ids are plain UUIDs (ADR-0026 §5). Route plans, rider execution and
handovers follow in later PRs.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "logistics"

VEHICLE_KINDS = ("cargo_3w", "two_wheeler")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Vehicle(Base):
    """A van or two-wheeler of one hub: boxes and add-ons ride cargo_3w, tiffins two_wheeler
    (ADR-0026 §6). A change applies to the next solve, never to a route already published."""

    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint(_one_of("kind", VEHICLE_KINDS), name="kind"),
        CheckConstraint("capacity_kg BETWEEN 10 AND 2000", name="capacity_kg"),
        CheckConstraint("capacity_crates BETWEEN 0 AND 100", name="capacity_crates"),
        CheckConstraint("capacity_tiffins BETWEEN 0 AND 100", name="capacity_tiffins"),
        CheckConstraint("version >= 1", name="version"),
        Index("uq_vehicles_hub_label", "hub_id", text("lower(label)"), unique=True),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M6's hub; no cross-schema FK
    label: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(16))
    registration: Mapped[str | None] = mapped_column(String(16))
    capacity_kg: Mapped[int] = mapped_column(SmallInteger)
    capacity_crates: Mapped[int] = mapped_column(SmallInteger)
    capacity_tiffins: Mapped[int] = mapped_column(SmallInteger)
    # identity.users.id of a rider of this hub; set on new routes.
    default_rider_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
