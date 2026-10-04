"""Snapshots (ADR-0025, M10 §6 `export_snapshots`): the module tables the models learn from,
read through the API's SQLAlchemy models with a read-only database role and written as Parquet
to `snapshots/date=<day>/<table>.parquet` in the lake.

No personal data leaves the database: households are exported without their owner, members or
address, and nothing from `identity.users`, `consents` or `addresses` is read at all. Every
table carries `data_source`, so synthetic rows are never pooled with real ones by accident.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Price, PriceList, Product
from gharbasket.modules.identity.models import Household
from gharbasket.modules.intel_gateway.models import Prediction, PriceIndex
from gharbasket.modules.orders.models import Order, OrderLine
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    EditFeedback,
    PantryFeedback,
    WeeklyCycle,
)
from gharbasket_training.lake import Lake


@dataclass(frozen=True)
class Table:
    name: str
    query: Callable[[], Any]  # a Select of the columns below
    columns: Sequence[str]
    kinds: dict[str, pa.DataType]  # the schema check: a column of another type fails the export


def _ts(x: datetime | None) -> datetime | None:
    return None if x is None else x.astimezone(UTC)


TABLES: tuple[Table, ...] = (
    Table(
        "households",
        lambda: select(
            Household.id,
            Household.society_id,
            Household.hub_id,
            Household.size,
            Household.diet,
            Household.cuisine_tags,
            Household.weekday_cook_minutes,
            Household.weekend_cook_minutes,
            Household.no_cook_days,
            Household.budget_paise,
            Household.autopilot_enabled,
            Household.auto_approve,
            Household.data_source,
            Household.created_at,
        ),
        (
            "id",
            "society_id",
            "hub_id",
            "size",
            "diet",
            "cuisine_tags",
            "weekday_cook_minutes",
            "weekend_cook_minutes",
            "no_cook_days",
            "budget_paise",
            "autopilot_enabled",
            "auto_approve",
            "data_source",
            "created_at",
        ),
        {"id": pa.string(), "size": pa.int64(), "data_source": pa.string()},
    ),
    Table(
        "weekly_cycles",
        lambda: select(
            WeeklyCycle.id,
            WeeklyCycle.household_id,
            WeeklyCycle.hub_id,
            WeeklyCycle.society_id,
            WeeklyCycle.week_start,
            WeeklyCycle.delivery_date,
            WeeklyCycle.cutoff_at,
            WeeklyCycle.plan_source,
            WeeklyCycle.status,
            WeeklyCycle.generated_at,
            WeeklyCycle.approved_at,
            WeeklyCycle.auto_approved,
            WeeklyCycle.order_id,
            WeeklyCycle.version,
            WeeklyCycle.data_source,
        ),
        (
            "id",
            "household_id",
            "hub_id",
            "society_id",
            "week_start",
            "delivery_date",
            "cutoff_at",
            "plan_source",
            "status",
            "generated_at",
            "approved_at",
            "auto_approved",
            "order_id",
            "version",
            "data_source",
        ),
        {"id": pa.string(), "status": pa.string(), "data_source": pa.string()},
    ),
    Table(
        "box_lines",
        lambda: select(
            BoxLine.cycle_id,
            BoxLine.product_id,
            BoxLine.qty,
            BoxLine.step_qty,
            BoxLine.step_unit,
            BoxLine.unit_price_paise,
            BoxLine.reason_code,
            BoxLine.source,
            BoxLine.replaces_product_id,
            WeeklyCycle.household_id,
            WeeklyCycle.delivery_date,
            WeeklyCycle.data_source,
        ).join(WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id),
        (
            "cycle_id",
            "product_id",
            "qty",
            "step_qty",
            "step_unit",
            "unit_price_paise",
            "reason_code",
            "source",
            "replaces_product_id",
            "household_id",
            "delivery_date",
            "data_source",
        ),
        {"qty": pa.int64(), "unit_price_paise": pa.int64()},
    ),
    Table(
        "edit_feedback",
        lambda: select(
            EditFeedback.id,
            EditFeedback.cycle_id,
            EditFeedback.product_id,
            EditFeedback.action,
            EditFeedback.substitute_product_id,
            EditFeedback.at,
            WeeklyCycle.household_id,
            WeeklyCycle.delivery_date,
            WeeklyCycle.data_source,
        ).join(WeeklyCycle, WeeklyCycle.id == EditFeedback.cycle_id),
        (
            "id",
            "cycle_id",
            "product_id",
            "action",
            "substitute_product_id",
            "at",
            "household_id",
            "delivery_date",
            "data_source",
        ),
        {"action": pa.string()},
    ),
    Table(
        "pantry_feedback",
        lambda: select(
            PantryFeedback.id,
            PantryFeedback.cycle_id,
            PantryFeedback.household_id,
            PantryFeedback.product_id,
            PantryFeedback.signal,
            PantryFeedback.at,
            WeeklyCycle.delivery_date,
            WeeklyCycle.data_source,
        ).join(WeeklyCycle, WeeklyCycle.id == PantryFeedback.cycle_id),
        (
            "id",
            "cycle_id",
            "household_id",
            "product_id",
            "signal",
            "at",
            "delivery_date",
            "data_source",
        ),
        {"signal": pa.string()},
    ),
    Table(
        "orders",
        lambda: select(
            Order.id,
            Order.household_id,
            Order.hub_id,
            Order.society_id,
            Order.kind,
            Order.cycle_id,
            Order.delivery_date,
            Order.slot_id,
            Order.cutoff_at,
            Order.status,
            Order.subtotal_paise,
            Order.fees_paise,
            Order.discount_paise,
            Order.total_paise,
            Order.locked_at,
            Order.data_source,
            Order.created_at,
        ),
        (
            "id",
            "household_id",
            "hub_id",
            "society_id",
            "kind",
            "cycle_id",
            "delivery_date",
            "slot_id",
            "cutoff_at",
            "status",
            "subtotal_paise",
            "fees_paise",
            "discount_paise",
            "total_paise",
            "locked_at",
            "data_source",
            "created_at",
        ),
        {"total_paise": pa.int64(), "status": pa.string()},
    ),
    Table(
        "order_lines",
        lambda: select(
            OrderLine.order_id,
            OrderLine.product_id,
            OrderLine.kind,
            OrderLine.qty,
            OrderLine.step_qty,
            OrderLine.step_unit,
            OrderLine.unit_price_paise,
            OrderLine.reason_code,
            Order.household_id,
            Order.delivery_date,
            Order.data_source,
        ).join(Order, Order.id == OrderLine.order_id),
        (
            "order_id",
            "product_id",
            "kind",
            "qty",
            "step_qty",
            "step_unit",
            "unit_price_paise",
            "reason_code",
            "household_id",
            "delivery_date",
            "data_source",
        ),
        {"qty": pa.int64()},
    ),
    Table(
        "products",
        lambda: select(
            Product.id,
            Product.slug,
            Product.kind,
            Product.category_id,
            Product.step_qty,
            Product.step_unit,
            Product.shelf_life_h,
            Product.tags,
            Product.active,
            Product.data_source,
        ),
        (
            "id",
            "slug",
            "kind",
            "category_id",
            "step_qty",
            "step_unit",
            "shelf_life_h",
            "tags",
            "active",
            "data_source",
        ),
        {"slug": pa.string()},
    ),
    Table(
        "prices",
        lambda: select(
            Price.price_list_id,
            PriceList.hub_id,
            PriceList.valid_from,
            Price.product_id,
            Price.price_paise,
            Price.mrp_paise,
            PriceList.data_source,
        ).join(PriceList, PriceList.id == Price.price_list_id),
        (
            "price_list_id",
            "hub_id",
            "valid_from",
            "product_id",
            "price_paise",
            "mrp_paise",
            "data_source",
        ),
        {"price_paise": pa.int64()},
    ),
    Table(
        "price_index",
        lambda: select(
            PriceIndex.product_id,
            PriceIndex.day,
            PriceIndex.market,
            PriceIndex.modal_paise_per_kg,
            PriceIndex.index_4w,
            PriceIndex.source,
        ),
        ("product_id", "day", "market", "modal_paise_per_kg", "index_4w", "source"),
        {"index_4w": pa.float64()},
    ),
    Table(
        "predictions",
        lambda: select(
            Prediction.id,
            Prediction.kind,
            Prediction.subject_type,
            Prediction.subject_id,
            Prediction.for_date,
            Prediction.source,
            Prediction.model_name,
            Prediction.model_version,
            Prediction.created_at,
        ),
        (
            "id",
            "kind",
            "subject_type",
            "subject_id",
            "for_date",
            "source",
            "model_name",
            "model_version",
            "created_at",
        ),
        {"kind": pa.string(), "source": pa.string()},
    ),
)


def _cell(value: Any) -> Any:
    if isinstance(value, datetime):
        return _ts(value)
    if hasattr(value, "hex") and not isinstance(value, bytes | str):  # a UUID
        return str(value)
    if isinstance(value, list):
        return [_cell(v) for v in value]
    return value


def to_table(table: Table, rows: Iterable[Sequence[Any]]) -> pa.Table:
    columns = {name: [] for name in table.columns}
    for row in rows:
        for name, value in zip(table.columns, row, strict=True):
            columns[name].append(_cell(value))
    arrow = pa.table(columns) if any(columns.values()) else _empty(table)
    for name, kind in table.kinds.items():
        got = arrow.schema.field(name).type
        if arrow.num_rows and got != kind:
            raise TypeError(f"{table.name}.{name}: expected {kind}, got {got}")
    return arrow


def _empty(table: Table) -> pa.Table:
    return pa.table(
        {name: pa.array([], type=table.kinds.get(name, pa.string())) for name in table.columns}
    )


async def export(session: AsyncSession, lake: Lake, day: date) -> dict[str, int]:
    """Write every table's snapshot for `day`; returns rows per table."""
    counts: dict[str, int] = {}
    for table in TABLES:
        rows = (await session.execute(table.query())).all()
        arrow = to_table(table, rows)
        lake.write(f"snapshots/date={day.isoformat()}/{table.name}.parquet", arrow)
        counts[table.name] = arrow.num_rows
    return counts
