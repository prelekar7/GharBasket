"""Waste (spec §3.5): every quantity that leaves stock other than by packing is a waste log, and
the KPI "kg wasted per 100 kg sold" is read from them. Hub staff write off a lot or a product
(always `cascade_to: "bin"`); the surplus cascade writes the other logs. Only `bin` is waste:
what kitchens and NGOs take is rescued.
"""

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Date, case, cast, func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import events, procurement, receiving, service, stock
from gharbasket.modules.hub_ops.models import PackingLine, PackingTask, WasteLog
from gharbasket.modules.hub_ops.schemas import (
    RescuedKg,
    TakenByKitchen,
    TakenByNgo,
    UserRef,
    WasteDayOut,
    WasteIn,
    WasteLogOut,
    WasteSummaryOut,
    WasteTotalsOut,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

MAX_DAYS = 31
ZERO, MILLI, CENT = Decimal(0), Decimal("0.001"), Decimal("0.01")


def _out_of_range(detail: str) -> ProblemError:
    return ProblemError(422, "hub_ops.date_out_of_range", detail)


def weigh(qty: Decimal, unit: str, unit_g: float | None) -> Decimal | None:
    """The weight in kg of a quantity in an operations unit: kg and l count as themselves (1 l is
    1 kg), anything else `qty * unit_g / 1000` from the catalog's nutrition data, else None."""
    if unit in ("kg", "l"):
        return qty
    if unit_g is None:
        return None
    return (qty * Decimal(str(unit_g)) / 1000).quantize(MILLI, ROUND_HALF_UP)


async def published(session: AsyncSession, log: WasteLog) -> None:
    """`hub_ops.waste_logged` for a log, written off or settled by the cascade."""
    await publish(
        session,
        events.WASTE_LOGGED,
        {
            "waste_log_id": str(log.id),
            "hub_id": str(log.hub_id),
            "lot_id": None if log.lot_id is None else str(log.lot_id),
            "product_id": str(log.product_id),
            "qty": float(log.qty),
            "unit": log.unit,
            "kg": None if log.kg is None else float(log.kg),
            "reason": log.reason,
            "cascade_to": log.cascade_to,
        },
    )


# --- writing off ---------------------------------------------------------------------------------


async def write_off(
    session: AsyncSession, hub_id: uuid.UUID, body: WasteIn, actor_user_id: uuid.UUID
) -> WasteLog:
    """Log a write-off (`cascade_to: "bin"`): with a lot, a `waste` move on the row-locked lot (not
    above its stock); with only a product, an unlotted move. Publishes `hub_ops.waste_logged`."""
    await service.get_hub(session, hub_id)
    lot = None
    if body.lot_id is not None:
        lot = await stock.get_lot(session, body.lot_id)
        if lot.hub_id != hub_id:
            raise stock.lot_not_found()
        lot = await stock.get_lot(session, lot.id, for_update=True)
        product_id = lot.product_id
    else:
        assert body.product_id is not None  # the schema requires one
        product_id = body.product_id
    product = (await catalog_api.stock_products(session, [product_id])).get(product_id)
    if product is None or product.kind not in procurement.STOCKED_KINDS:
        raise ProblemError(
            422,
            "hub_ops.product_not_stocked",
            "The hub does not stock this product.",
            extra={"product_ids": [str(product_id)]},
        )
    unit = procurement.OPS_UNIT[product.step_unit][0] if lot is None else lot.unit
    qty = procurement.check_qty(body.qty, unit)
    if qty == 0:
        raise ProblemError(422, "hub_ops.qty_invalid", "A written-off quantity is above 0.")
    if lot is not None and qty > lot.qty_on_hand:
        raise ProblemError(
            409,
            "hub_ops.qty_exceeds_stock",
            "The lot holds less than that.",
            extra={"qty_on_hand": float(lot.qty_on_hand)},
        )
    log = WasteLog(
        hub_id=hub_id,
        lot_id=None if lot is None else lot.id,
        product_id=product_id,
        qty=qty,
        unit=unit,
        kg=weigh(qty, unit, product.unit_g),
        reason=body.reason,
        cascade_to="bin",
        note=body.note,
        logged_by=actor_user_id,
    )
    session.add(log)
    await session.flush()
    await receiving.move(
        session,
        hub_id=hub_id,
        product_id=product_id,
        unit=unit,
        kind="waste",
        ref_id=log.id,
        qty=-qty,
        lot=lot,
        reason=body.reason,
        by_user_id=actor_user_id,
    )
    await session.refresh(log)
    await published(session, log)
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="waste_logs.created",
        entity_type="waste_log",
        entity_id=str(log.id),
        after=body.model_dump(mode="json"),
    )
    return log


# --- reading -------------------------------------------------------------------------------------


def _cursor_error() -> ProblemError:
    return ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")


def decode_position(cursor: str) -> tuple[datetime, uuid.UUID]:
    position = decode_cursor(cursor)
    try:
        return datetime.fromisoformat(position["at"]), uuid.UUID(position["id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _cursor_error() from exc


def encode_position(log: WasteLog) -> str:
    return encode_cursor({"at": log.at.isoformat(), "id": str(log.id)})


async def list_logs(
    session: AsyncSession,
    hub_id: uuid.UUID,
    *,
    date_from: date | None,
    date_to: date | None,
    cascade_to: str | None,
    after: tuple[datetime, uuid.UUID] | None,
    limit: int,
) -> list[WasteLog]:
    """The hub's logs of the IST days `date_from` … `date_to` (inclusive), newest first."""
    if date_from is not None and date_to is not None and date_from > date_to:
        raise _out_of_range("`from` is after `to`.")
    stmt = select(WasteLog).where(WasteLog.hub_id == hub_id)
    if date_from is not None:
        stmt = stmt.where(WasteLog.at >= receiving.day_bounds(date_from)[0])
    if date_to is not None:
        stmt = stmt.where(WasteLog.at < receiving.day_bounds(date_to)[1])
    if cascade_to is not None:
        stmt = stmt.where(WasteLog.cascade_to == cascade_to)
    if after is not None:
        stmt = stmt.where(tuple_(WasteLog.at, WasteLog.id) < tuple_(*after))
    rows = await session.scalars(stmt.order_by(WasteLog.at.desc(), WasteLog.id.desc()).limit(limit))
    return list(rows)


def taker_of(log: WasteLog) -> TakenByKitchen | TakenByNgo | None:
    if log.taken_by_kind == "kitchen" and log.taken_by_partner_id is not None:
        return TakenByKitchen(
            kind="kitchen", partner_id=log.taken_by_partner_id, name=log.taken_by_name or ""
        )
    if log.taken_by_kind == "ngo":
        return TakenByNgo(kind="ngo", name=log.taken_by_name or "")
    return None


async def logs_out(
    session: AsyncSession, logs: list[WasteLog], lang: Language
) -> list[WasteLogOut]:
    products = await catalog_api.stock_products(session, list({x.product_id for x in logs}))
    users = await identity_api.user_contacts(
        session, [x.logged_by for x in logs if x.logged_by is not None]
    )

    def out(x: WasteLog) -> WasteLogOut:
        product = products[x.product_id]
        by = None
        if x.logged_by is not None:
            user = users.get(x.logged_by)
            by = UserRef(user_id=x.logged_by, name=None if user is None else user.name)
        taken_by = taker_of(x)
        return WasteLogOut.model_validate(
            {
                **x.as_dict(
                    exclude=("taken_by_kind", "taken_by_partner_id", "taken_by_name", "logged_by")
                ),
                "slug": product.slug,
                "name": localised(product.names, lang),
                "taken_by": taken_by,
                "logged_by": by,
            }
        )

    return [out(x) for x in logs]


# --- the KPI -------------------------------------------------------------------------------------


@dataclass
class DayStats:
    """One IST day of the waste summary, in exact quantities."""

    date: date
    sold_kg: Decimal = ZERO
    wasted_kg: Decimal = ZERO
    rescued_kg: dict[str, Decimal] = field(default_factory=lambda: {"kitchen": ZERO, "ngo": ZERO})
    ordered_steps: int = 0
    affected_steps: int = 0  # short or substituted
    unweighed_lines: int = 0

    @property
    def waste_per_100kg(self) -> Decimal | None:
        if self.sold_kg == 0:
            return None
        return (self.wasted_kg / self.sold_kg * 100).quantize(CENT, ROUND_HALF_UP)

    @property
    def stockout_pct(self) -> Decimal | None:
        if self.ordered_steps == 0:
            return None
        return (Decimal(self.affected_steps) / self.ordered_steps * 100).quantize(
            CENT, ROUND_HALF_UP
        )


def summary_range(date_from: date | None, date_to: date | None) -> tuple[date, date]:
    """The days asked for, at most 31; the default is the last 7 up to yesterday (IST)."""
    end = date_to or datetime.now(procurement.IST).date() - timedelta(days=1)
    start = date_from or end - timedelta(days=6)
    if start > end or (end - start).days >= MAX_DAYS:
        raise _out_of_range("Ask for 1 to 31 days, `from` not after `to`.")
    return start, end


async def summary_days(
    session: AsyncSession, hub_id: uuid.UUID, date_from: date, date_to: date
) -> list[DayStats]:
    """A row per IST day from `date_from` to `date_to`, zeros included. Sold: the picked steps of
    the packed tasks of that delivery date (not the carried lines, sold on an earlier day),
    weighed as for waste logs. Stock-out: steps short or substituted over steps ordered, on all of
    the day's tasks. A substituted line counts the substitute's own step (the packing line keeps
    the ordered product's)."""
    days = {
        date_from + timedelta(days=i): DayStats(date_from + timedelta(days=i))
        for i in range((date_to - date_from).days + 1)
    }
    substituted = PackingLine.substitute_product_id.is_not(None)
    lines = (
        await session.execute(
            select(
                PackingTask.delivery_date,
                PackingTask.status,
                PackingLine.product_id,
                PackingLine.substitute_product_id,
                PackingLine.step_qty,
                PackingLine.step_unit,
                func.sum(PackingLine.qty),
                func.sum(PackingLine.picked_qty),
                func.sum(case((substituted, PackingLine.qty), else_=PackingLine.short_qty)),
                func.count().filter(PackingLine.picked_qty > 0),
            )
            .join(PackingTask, PackingTask.id == PackingLine.task_id)
            .where(
                PackingTask.hub_id == hub_id,
                PackingTask.delivery_date.between(date_from, date_to),
                PackingLine.carried.is_(False),
            )
            .group_by(
                PackingTask.delivery_date,
                PackingTask.status,
                PackingLine.product_id,
                PackingLine.substitute_product_id,
                PackingLine.step_qty,
                PackingLine.step_unit,
            )
        )
    ).all()
    products = await catalog_api.stock_products(session, list({row[3] or row[2] for row in lines}))
    for (
        day,
        task_status,
        product_id,
        substitute_id,
        step_qty,
        step_unit,
        qty,
        picked,
        hit,
        n,
    ) in lines:
        stats = days[day]
        stats.ordered_steps += int(qty)
        stats.affected_steps += int(hit)
        if task_status != "packed" or not picked:
            continue
        product = products.get(substitute_id or product_id)
        if substitute_id is not None:
            # The packer picked the substitute's own steps (M4 gives the line its step); the
            # packing line keeps the ordered product's.
            if product is None:
                stats.unweighed_lines += n
                continue
            step_qty, step_unit = product.step_qty, product.step_unit
        unit, _ = procurement.OPS_UNIT[step_unit]
        kg = weigh(
            procurement.to_ops(int(picked), step_qty, step_unit),
            unit,
            None if product is None else product.unit_g,
        )
        if kg is None:
            stats.unweighed_lines += n
        else:
            stats.sold_kg += kg
    start, end = receiving.day_bounds(date_from)[0], receiving.day_bounds(date_to)[1]
    ist_day = cast(func.timezone("Asia/Kolkata", WasteLog.at), Date)
    logged = await session.execute(
        select(
            ist_day,
            WasteLog.cascade_to,
            func.coalesce(func.sum(WasteLog.kg), 0),
            func.count().filter(WasteLog.kg.is_(None)),
        )
        .where(WasteLog.hub_id == hub_id, WasteLog.at >= start, WasteLog.at < end)
        .group_by(ist_day, WasteLog.cascade_to)
    )
    for day, cascade_to, kg, unweighed in logged.all():
        stats = days[day]
        stats.unweighed_lines += unweighed  # a log with no weight is left out as well
        if cascade_to == "bin":
            stats.wasted_kg += kg or ZERO
        else:
            stats.rescued_kg[cascade_to] += kg or ZERO
    return sorted(days.values(), key=lambda x: x.date)


def total(days: list[DayStats]) -> DayStats:
    """The days added up (dated like the last one)."""
    out = DayStats(days[-1].date)
    for x in days:
        out.sold_kg += x.sold_kg
        out.wasted_kg += x.wasted_kg
        for kind, kg in x.rescued_kg.items():
            out.rescued_kg[kind] += kg
        out.ordered_steps += x.ordered_steps
        out.affected_steps += x.affected_steps
        out.unweighed_lines += x.unweighed_lines
    return out


def _kg(value: Decimal) -> float:
    return float(value.quantize(MILLI, ROUND_HALF_UP))


def _opt(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def summary_out(
    hub_id: uuid.UUID, date_from: date, date_to: date, days: list[DayStats]
) -> WasteSummaryOut:
    def rescued(x: DayStats) -> RescuedKg:
        return RescuedKg(kitchen=_kg(x.rescued_kg["kitchen"]), ngo=_kg(x.rescued_kg["ngo"]))

    sums = total(days)
    day_rows = [
        WasteDayOut(
            date=x.date,
            sold_kg=_kg(x.sold_kg),
            wasted_kg=_kg(x.wasted_kg),
            rescued_kg=rescued(x),
            waste_per_100kg=_opt(x.waste_per_100kg),
            stockout_pct=_opt(x.stockout_pct),
            unweighed_lines=x.unweighed_lines,
        )
        for x in days
    ]
    totals = WasteTotalsOut(
        sold_kg=_kg(sums.sold_kg),
        wasted_kg=_kg(sums.wasted_kg),
        rescued_kg=rescued(sums),
        waste_per_100kg=_opt(sums.waste_per_100kg),
        stockout_pct=_opt(sums.stockout_pct),
        unweighed_lines=sums.unweighed_lines,
    )
    return WasteSummaryOut.model_validate(
        {"hub_id": hub_id, "from": date_from, "to": date_to, "days": day_rows, "totals": totals}
    )
