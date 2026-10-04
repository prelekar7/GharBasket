"""Tables of M10 Intelligence, in the `intel` schema (M10 §4). The API writes them through
`intel_gateway.service`; the ML service itself is stateless (ADR-0025).

- `predictions`: every answer the ML service (or its rule fallback) gave, by kind and subject, so
  the app can show what was planned and the paper can replay what was decided.
- `model_metrics`: evaluation and monitoring numbers per model version, split and data source.
- `feature_snapshots`: point-in-time features (as of a cut-off), so training never sees the future.
- `experiments`: which variant a subject was assigned in an A/B test.
- `price_index`: today's wholesale price per product and market against its 4-week median, from
  the mandi collector (backend/training); the planner's price-spike penalty reads it.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "intel"

PREDICTION_KINDS = (
    "weekly_plan",
    "pantry",
    "basket_rank",
    "forecast",
    "route",
    "assignment",
    "slot_price",
    "churn",
)
SUBJECT_TYPES = ("household", "hub", "society", "order", "partner", "product")
SOURCES = ("ml", "fallback")
SPLITS = ("train", "holdout", "backtest", "live")
DATA_SOURCES = ("synthetic", "pilot", "live", "mixed")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Prediction(Base):
    __tablename__ = "predictions"
    __table_args__ = (
        CheckConstraint(_one_of("kind", PREDICTION_KINDS), name="kind"),
        CheckConstraint(_one_of("subject_type", SUBJECT_TYPES), name="subject_type"),
        CheckConstraint(_one_of("source", SOURCES), name="source"),
        Index("ix_predictions_subject", "kind", "subject_id", "for_date"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    kind: Mapped[str] = mapped_column(String(20))
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # another module's row; no cross-schema FK
    for_date: Mapped[date] = mapped_column(Date)  # the week's Monday, the delivery day, …
    source: Mapped[str] = mapped_column(String(10))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    model_name: Mapped[str] = mapped_column(String(60))
    model_version: Mapped[str | None] = mapped_column(String(40))
    request_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ModelMetric(Base):
    __tablename__ = "model_metrics"
    __table_args__ = (
        CheckConstraint(_one_of("split", SPLITS), name="split"),
        CheckConstraint(_one_of("data_source", DATA_SOURCES), name="data_source"),
        Index("ix_model_metrics_model", "model_name", "metric", "computed_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    model_name: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(40))
    metric: Mapped[str] = mapped_column(String(40))  # e.g. "wape", "ndcg_at_10", "acceptance"
    value: Mapped[float] = mapped_column(Float)
    split: Mapped[str] = mapped_column(String(10))
    data_source: Mapped[str] = mapped_column(String(10))
    # Seeds, snapshot date, simulator version, n: whatever makes the number reproducible.
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FeatureSnapshot(Base):
    __tablename__ = "feature_snapshots"
    __table_args__ = (
        CheckConstraint(_one_of("subject_type", SUBJECT_TYPES), name="subject_type"),
        {"schema": SCHEMA},
    )

    subject_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Experiment(Base):
    __tablename__ = "experiments"
    __table_args__ = (
        CheckConstraint(_one_of("subject_type", SUBJECT_TYPES), name="subject_type"),
        UniqueConstraint("key", "subject_type", "subject_id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    key: Mapped[str] = mapped_column(String(60))  # e.g. "planner_v1_vs_rules"
    variant: Mapped[str] = mapped_column(String(40))
    subject_type: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PriceIndex(Base):
    __tablename__ = "price_index"
    __table_args__ = (
        CheckConstraint("modal_paise_per_kg > 0", name="modal_paise_per_kg"),
        CheckConstraint("index_4w > 0", name="index_4w"),
        Index("ix_price_index_day", "day"),
        {"schema": SCHEMA},
    )

    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)  # catalog.products
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    market: Mapped[str] = mapped_column(String(80), primary_key=True)
    modal_paise_per_kg: Mapped[int] = mapped_column(Integer)
    # Today's modal price over the median of the previous 4 weeks: 1.62 means 62 % above usual.
    index_4w: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(20), server_default="agmarknet")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
