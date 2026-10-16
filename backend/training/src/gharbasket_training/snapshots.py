"""Snapshots (ADR-0025, M10 §6 `export_snapshots`): the module tables the models learn from,
read through the API's SQLAlchemy models with a read-only database role and written as Parquet
to `snapshots/date=<day>/<table>.parquet` in the lake.

No personal data leaves the database: households are exported without their owner, members or
address, and nothing from `identity.users`, `consents` or `addresses` is read at all. The
quasi-identifiers are coarsened: no society anywhere (the hub is the only geography) and the
monthly budget as a band, not in paise. Rows of a household that no longer exists (an erasure)
are left out of every table. Every table carries `data_source`, so synthetic rows are never
pooled with real ones by accident.

A partition `date=<day>` holds the rows created (or, for events, happened) before the end of
that day in IST, so a backfill of a past day never sees later rows. Each table names the column
that decides this (`Table.at`); a table without such a column cannot be exported. Mutable state
(a cycle's status, an order's total) is read as it is now: a backfill is the rows that existed
then, with their current values.

Every table has a fixed Arrow schema: an empty table and a column of nulls keep their types, and
a value of another type fails the export.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pyarrow as pa
from sqlalchemy import Select, case, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

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

IST = ZoneInfo("Asia/Kolkata")

UUID = pa.string()
TS = pa.timestamp("us", tz="UTC")
DAY = pa.date32()
INT = pa.int64()
STR = pa.string()
BOOL = pa.bool_()

# The monthly Budget Lock in bands (rupees): the amount itself is a quasi-identifier.
BUDGET_BANDS = ((300_000, "lt_3k"), (500_000, "3k_5k"), (800_000, "5k_8k"), (1_200_000, "8k_12k"))
BUDGET_BAND = case(
    (Household.budget_paise.is_(None), None),
    *((Household.budget_paise < upper, label) for upper, label in BUDGET_BANDS),
    else_="12k_plus",
)


def _live(household_id: Any) -> ColumnElement[bool]:
    """Only households that still exist: an erased household leaves every later snapshot."""
    return household_id.in_(select(Household.id))


@dataclass(frozen=True)
class Table:
    name: str
    columns: Sequence[tuple[str, Any, pa.DataType]]  # (name, SQL expression, Arrow type)
    at: Any  # the creation or event column: a row is in `date=<day>` if it is before day's end
    joins: Sequence[tuple[Any, Any]] = ()  # (model, on clause)
    where: Sequence[Any] = ()

    @property
    def schema(self) -> pa.Schema:
        return pa.schema([(name, kind) for name, _, kind in self.columns])

    @property
    def column_names(self) -> list[str]:
        return [name for name, _, _ in self.columns]

    def query(self, until: datetime) -> Select[*tuple[Any, ...]]:
        """The rows created before `until` (an instant; a date column compares by IST day)."""
        stmt = select(*(expr for _, expr, _ in self.columns))
        for model, on in self.joins:
            stmt = stmt.join(model, on)
        bound = until.astimezone(IST).date() if self.at.type.python_type is date else until
        return stmt.where(self.at < bound, *self.where)


TABLES: tuple[Table, ...] = (
    Table(
        "households",
        (
            ("id", Household.id, UUID),
            ("hub_id", Household.hub_id, UUID),
            ("size", Household.size, INT),
            ("diet", Household.diet, STR),
            ("cuisine_tags", Household.cuisine_tags, pa.list_(STR)),
            ("weekday_cook_minutes", Household.weekday_cook_minutes, INT),
            ("weekend_cook_minutes", Household.weekend_cook_minutes, INT),
            ("no_cook_days", Household.no_cook_days, pa.list_(INT)),
            ("budget_band", BUDGET_BAND, STR),
            ("autopilot_enabled", Household.autopilot_enabled, BOOL),
            ("auto_approve", Household.auto_approve, BOOL),
            ("data_source", Household.data_source, STR),
            ("created_at", Household.created_at, TS),
        ),
        at=Household.created_at,
    ),
    Table(
        "weekly_cycles",
        (
            ("id", WeeklyCycle.id, UUID),
            ("household_id", WeeklyCycle.household_id, UUID),
            ("hub_id", WeeklyCycle.hub_id, UUID),
            ("week_start", WeeklyCycle.week_start, DAY),
            ("delivery_date", WeeklyCycle.delivery_date, DAY),
            ("cutoff_at", WeeklyCycle.cutoff_at, TS),
            ("plan_source", WeeklyCycle.plan_source, STR),
            ("status", WeeklyCycle.status, STR),
            ("generated_at", WeeklyCycle.generated_at, TS),
            ("approved_at", WeeklyCycle.approved_at, TS),
            ("auto_approved", WeeklyCycle.auto_approved, BOOL),
            ("order_id", WeeklyCycle.order_id, UUID),
            ("version", WeeklyCycle.version, INT),
            ("data_source", WeeklyCycle.data_source, STR),
        ),
        at=WeeklyCycle.generated_at,
        where=(_live(WeeklyCycle.household_id),),
    ),
    Table(
        "box_lines",
        (
            ("cycle_id", BoxLine.cycle_id, UUID),
            ("product_id", BoxLine.product_id, UUID),
            ("qty", BoxLine.qty, INT),
            ("step_qty", BoxLine.step_qty, INT),
            ("step_unit", BoxLine.step_unit, STR),
            ("unit_price_paise", BoxLine.unit_price_paise, INT),
            ("reason_code", BoxLine.reason_code, STR),
            ("source", BoxLine.source, STR),
            ("replaces_product_id", BoxLine.replaces_product_id, UUID),
            ("household_id", WeeklyCycle.household_id, UUID),
            ("delivery_date", WeeklyCycle.delivery_date, DAY),
            ("data_source", WeeklyCycle.data_source, STR),
        ),
        at=WeeklyCycle.generated_at,
        joins=((WeeklyCycle, WeeklyCycle.id == BoxLine.cycle_id),),
        where=(_live(WeeklyCycle.household_id),),
    ),
    Table(
        "edit_feedback",
        (
            ("id", EditFeedback.id, UUID),
            ("cycle_id", EditFeedback.cycle_id, UUID),
            ("product_id", EditFeedback.product_id, UUID),
            ("action", EditFeedback.action, STR),
            ("substitute_product_id", EditFeedback.substitute_product_id, UUID),
            ("at", EditFeedback.at, TS),
            ("household_id", WeeklyCycle.household_id, UUID),
            ("delivery_date", WeeklyCycle.delivery_date, DAY),
            ("data_source", WeeklyCycle.data_source, STR),
        ),
        at=EditFeedback.at,
        joins=((WeeklyCycle, WeeklyCycle.id == EditFeedback.cycle_id),),
        where=(_live(WeeklyCycle.household_id),),
    ),
    Table(
        "pantry_feedback",
        (
            ("id", PantryFeedback.id, UUID),
            ("cycle_id", PantryFeedback.cycle_id, UUID),
            ("household_id", PantryFeedback.household_id, UUID),
            ("product_id", PantryFeedback.product_id, UUID),
            ("signal", PantryFeedback.signal, STR),
            ("at", PantryFeedback.at, TS),
            ("delivery_date", WeeklyCycle.delivery_date, DAY),
            ("data_source", WeeklyCycle.data_source, STR),
        ),
        at=PantryFeedback.at,
        joins=((WeeklyCycle, WeeklyCycle.id == PantryFeedback.cycle_id),),
        where=(_live(PantryFeedback.household_id),),
    ),
    Table(
        "orders",
        (
            ("id", Order.id, UUID),
            ("household_id", Order.household_id, UUID),
            ("hub_id", Order.hub_id, UUID),
            ("kind", Order.kind, STR),
            ("cycle_id", Order.cycle_id, UUID),
            ("delivery_date", Order.delivery_date, DAY),
            ("slot_id", Order.slot_id, UUID),
            ("cutoff_at", Order.cutoff_at, TS),
            ("status", Order.status, STR),
            ("subtotal_paise", Order.subtotal_paise, INT),
            ("fees_paise", Order.fees_paise, INT),
            ("discount_paise", Order.discount_paise, INT),
            ("total_paise", Order.total_paise, INT),
            ("locked_at", Order.locked_at, TS),
            ("data_source", Order.data_source, STR),
            ("created_at", Order.created_at, TS),
        ),
        at=Order.created_at,
        where=(_live(Order.household_id),),
    ),
    Table(
        "order_lines",
        (
            ("order_id", OrderLine.order_id, UUID),
            ("product_id", OrderLine.product_id, UUID),
            ("kind", OrderLine.kind, STR),
            ("qty", OrderLine.qty, INT),
            ("step_qty", OrderLine.step_qty, INT),
            ("step_unit", OrderLine.step_unit, STR),
            ("unit_price_paise", OrderLine.unit_price_paise, INT),
            ("reason_code", OrderLine.reason_code, STR),
            ("household_id", Order.household_id, UUID),
            ("delivery_date", Order.delivery_date, DAY),
            ("data_source", Order.data_source, STR),
        ),
        at=Order.created_at,
        joins=((Order, Order.id == OrderLine.order_id),),
        where=(_live(Order.household_id),),
    ),
    Table(
        "products",
        (
            ("id", Product.id, UUID),
            ("slug", Product.slug, STR),
            ("kind", Product.kind, STR),
            ("category_id", Product.category_id, UUID),
            ("step_qty", Product.step_qty, INT),
            ("step_unit", Product.step_unit, STR),
            ("shelf_life_h", Product.shelf_life_h, INT),
            ("tags", Product.tags, pa.list_(STR)),
            ("active", Product.active, BOOL),
            ("data_source", Product.data_source, STR),
        ),
        at=Product.created_at,
    ),
    Table(
        "prices",
        (
            ("price_list_id", Price.price_list_id, UUID),
            ("hub_id", PriceList.hub_id, UUID),
            ("valid_from", PriceList.valid_from, DAY),
            ("product_id", Price.product_id, UUID),
            ("price_paise", Price.price_paise, INT),
            ("mrp_paise", Price.mrp_paise, INT),
            ("data_source", PriceList.data_source, STR),
        ),
        at=PriceList.created_at,
        joins=((PriceList, PriceList.id == Price.price_list_id),),
    ),
    Table(
        "price_index",
        (
            ("product_id", PriceIndex.product_id, UUID),
            ("day", PriceIndex.day, DAY),
            ("market", PriceIndex.market, STR),
            ("modal_paise_per_kg", PriceIndex.modal_paise_per_kg, INT),
            ("index_4w", PriceIndex.index_4w, pa.float64()),
            ("source", PriceIndex.source, STR),
        ),
        at=PriceIndex.day,  # the market day the price is for
    ),
    Table(
        "predictions",
        (
            ("id", Prediction.id, UUID),
            ("kind", Prediction.kind, STR),
            ("subject_type", Prediction.subject_type, STR),
            ("subject_id", Prediction.subject_id, UUID),
            ("for_date", Prediction.for_date, DAY),
            ("source", Prediction.source, STR),
            ("model_name", Prediction.model_name, STR),
            ("model_version", Prediction.model_version, STR),
            ("created_at", Prediction.created_at, TS),
        ),
        at=Prediction.created_at,
        where=(or_(Prediction.subject_type != "household", _live(Prediction.subject_id)),),
    ),
)


def end_of(day: date) -> datetime:
    """The end of `day` in IST (midnight starting the next day), as a UTC instant."""
    return datetime.combine(day + timedelta(days=1), time(0, 0), IST).astimezone(UTC)


def _cell(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list):
        return [_cell(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
    return value


def to_table(table: Table, rows: Iterable[Sequence[Any]]) -> pa.Table:
    """The rows as an Arrow table with the table's schema; a value of another type raises."""
    names = table.column_names
    values: dict[str, list[Any]] = {name: [] for name in names}
    for row in rows:
        for name, value in zip(names, row, strict=True):
            values[name].append(_cell(value))
    arrays: dict[str, pa.Array] = {}
    for field in table.schema:
        try:
            arrays[field.name] = pa.array(values[field.name], type=field.type)
        except (TypeError, ValueError, OverflowError) as e:  # Arrow's errors subclass these
            raise TypeError(f"{table.name}.{field.name}: expected {field.type} ({e})") from e
    return pa.table(arrays, schema=table.schema)


async def read_consistently(session: AsyncSession) -> None:
    """Make the session's transaction one read-only snapshot of the database: every table (and
    the features after them) sees the same moment. The first statement of the transaction."""
    await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))


async def export(session: AsyncSession, lake: Lake, day: date) -> dict[str, int]:
    """Write every table's snapshot for `day` (rows before the end of that IST day); returns
    rows per table. Run it inside `read_consistently` so all tables come from one snapshot."""
    until = end_of(day)
    counts: dict[str, int] = {}
    for table in TABLES:
        rows = (await session.execute(table.query(until))).all()
        arrow = to_table(table, rows)
        lake.write(f"snapshots/date={day.isoformat()}/{table.name}.parquet", arrow)
        counts[table.name] = arrow.num_rows
    return counts
