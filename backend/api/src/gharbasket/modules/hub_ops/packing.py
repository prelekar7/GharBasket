"""Packing (spec §3.2, ADR-0026 §6): tasks built at T 05:00 in delivery order, the queue, the
summary per society and a task with its lines.

Orders belong to M4: tasks are built from `orders.api.orders_for_delivery`, with addresses from
identity, society names from community and product names from the catalog, all through their
facades. M6 never writes `orders.*`.
"""

import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import service
from gharbasket.modules.hub_ops.models import PackingLine, PackingTask
from gharbasket.modules.hub_ops.schemas import (
    Packer,
    PackingLineOut,
    PackingRoute,
    PackingSlot,
    PackingSociety,
    PackingStep,
    PackingSubstitute,
    PackingTaskDetail,
    PackingTaskOut,
    SocietyLoad,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.permissions import Permit

IST = ZoneInfo("Asia/Kolkata")
PACKED_KINDS = ("box", "addon")  # tiffins are cooked by kitchens (M8), never packed here
DAYS_BACK, DAYS_AHEAD = 7, 2


def _problem(status: int, code: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(status, code, detail, extra=extra or None)


def task_not_found() -> ProblemError:
    return _problem(404, "hub_ops.task_not_found", "No such packing task.")


def bag_qr(order_id: uuid.UUID) -> str:
    """Printed on the bag label; the captain scans it at the door."""
    return f"GB1:{order_id.hex}"


def packing_day(day: date | None) -> date:
    """`day` (default today in IST), or 422 `hub_ops.date_out_of_range`."""
    today = datetime.now(IST).date()
    day = day or today
    if not today - timedelta(days=DAYS_BACK) <= day <= today + timedelta(days=DAYS_AHEAD):
        raise _problem(
            422, "hub_ops.date_out_of_range", "Pick a date from a week ago to 2 days on."
        )
    return day


# --- building the queue ---------------------------------------------------------------------------


def _natural(text: str) -> tuple[Any, ...]:
    """Digit runs compare as numbers, so A-101 comes before A-1203."""
    return tuple(
        int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", text)
    )


async def build(session: AsyncSession, hub_id: uuid.UUID, day: date) -> tuple[int, int]:
    """One task per locked box or add-on order of the hub and date that has none yet; returns
    (created, existing). New tasks join the end of the queue, sorted by slot start, society
    name, tower and flat. Route order (M7's `logistics.route_published`, not built yet) will
    re-sequence queued tasks; `route_id` and `stop_seq` stay null until then."""
    await service.get_hub(session, hub_id, for_update=True)  # one build per hub at a time
    have = (
        await session.execute(
            select(PackingTask.order_id).where(
                PackingTask.hub_id == hub_id, PackingTask.delivery_date == day
            )
        )
    ).scalars()
    existing = set(have)
    found = await orders_api.orders_for_delivery(
        session, hub_id, day, statuses=("locked",), kinds=PACKED_KINDS
    )
    new = [summary for summary in found if summary.order.id not in existing]
    if not new:
        return 0, len(existing)

    orders = [summary.order for summary in new]
    labels = await identity_api.delivery_labels(session, [o.household_id for o in orders])
    societies = await community_api.society_labels(session, list({o.society_id for o in orders}))
    slots = {s: await orders_api.slot_ref(session, s) for s in {o.slot_id for o in orders}}

    def place(summary: orders_api.OrderSummary) -> tuple[Any, ...]:
        order = summary.order
        slot, society = slots.get(order.slot_id), societies.get(order.society_id)
        label = labels.get(order.household_id)
        towers = society.towers if society else {}
        tower = towers.get(label.tower_id, label.tower_id) if label else ""
        return (
            slot.start if slot else time.max,
            society.name.casefold() if society else "",
            _natural(tower),
            _natural(label.flat if label else ""),
            order.id,
        )

    seq = (
        await session.scalar(
            select(func.max(PackingTask.seq)).where(
                PackingTask.hub_id == hub_id, PackingTask.delivery_date == day
            )
        )
    ) or 0
    for summary in sorted(new, key=place):
        order, label = summary.order, labels.get(summary.order.household_id)
        seq += 1
        task = PackingTask(
            hub_id=hub_id,
            order_id=order.id,
            order_kind=order.kind,
            delivery_date=day,
            slot_id=order.slot_id,
            society_id=order.society_id,
            tower_id=label.tower_id if label else None,
            flat=label.flat if label else None,
            seq=seq,
        )
        session.add(task)
        await session.flush()
        session.add_all(
            PackingLine(
                task_id=task.id,
                product_id=line.substituted_from or line.product_id,
                step_qty=line.step_qty,
                step_unit=line.step_unit,
                qty=line.qty,
                short_qty=line.shortage_qty,
                substitute_product_id=line.product_id if line.substituted_from else None,
            )
            for line in summary.lines
        )
    await session.flush()
    return len(new), len(existing)


# --- reading ------------------------------------------------------------------------------------


async def list_tasks(
    session: AsyncSession,
    hub_id: uuid.UUID,
    day: date,
    *,
    status: str | None,
    society_id: uuid.UUID | None,
    after_seq: int | None,
    limit: int,
) -> list[PackingTask]:
    stmt = select(PackingTask).where(PackingTask.hub_id == hub_id, PackingTask.delivery_date == day)
    if status is not None:
        stmt = stmt.where(PackingTask.status == status)
    if society_id is not None:
        stmt = stmt.where(PackingTask.society_id == society_id)
    if after_seq is not None:
        stmt = stmt.where(PackingTask.seq > after_seq)
    rows = await session.execute(stmt.order_by(PackingTask.seq).limit(limit))
    return list(rows.scalars())


async def status_counts(session: AsyncSession, hub_id: uuid.UUID, day: date) -> dict[str, int]:
    rows = await session.execute(
        select(PackingTask.status, func.count())
        .where(PackingTask.hub_id == hub_id, PackingTask.delivery_date == day)
        .group_by(PackingTask.status)
    )
    return {
        "queued": 0,
        "picking": 0,
        "packed": 0,
        **{status: count for status, count in rows.all()},
    }


async def society_loads(session: AsyncSession, hub_id: uuid.UUID, day: date) -> list[SocietyLoad]:
    """Bags, packed bags and crates per society, by society name."""
    packed = PackingTask.status == "packed"
    result = await session.execute(
        select(
            PackingTask.society_id,
            func.sum(PackingTask.bags),
            func.coalesce(func.sum(case((packed, PackingTask.bags))), 0),
            func.coalesce(func.max(case((packed, PackingTask.crate_no))), 0),
        )
        .where(PackingTask.hub_id == hub_id, PackingTask.delivery_date == day)
        .group_by(PackingTask.society_id)
    )
    rows = result.all()
    names = await community_api.society_labels(session, [row[0] for row in rows])
    loads = [
        SocietyLoad(
            society_id=society_id,
            name=names[society_id].name if society_id in names else "",
            bags=int(bags),
            packed_bags=int(packed_bags),
            crates=int(crates),
        )
        for society_id, bags, packed_bags, crates in rows
    ]
    return sorted(loads, key=lambda load: (load.name.casefold(), load.society_id))


async def lines_of(session: AsyncSession, task_id: uuid.UUID) -> list[PackingLine]:
    rows = await session.execute(select(PackingLine).where(PackingLine.task_id == task_id))
    return list(rows.scalars())


async def views(session: AsyncSession, tasks: list[PackingTask]) -> list[PackingTaskOut]:
    """Tasks as the API shows them: slot, society, tower label, packer and line counts."""
    if not tasks:
        return []
    ids = [task.id for task in tasks]
    done = PackingLine.picked_qty + PackingLine.short_qty == PackingLine.qty
    counts = {
        task_id: (total, finished)
        for task_id, total, finished in (
            await session.execute(
                select(PackingLine.task_id, func.count(), func.count().filter(done))
                .where(PackingLine.task_id.in_(ids))
                .group_by(PackingLine.task_id)
            )
        ).all()
    }
    societies = await community_api.society_labels(session, list({t.society_id for t in tasks}))
    packers = {t.packer_user_id for t in tasks if t.packer_user_id is not None}
    names = await identity_api.user_names(session, list(packers))
    slots: dict[uuid.UUID, PackingSlot | None] = {}
    for slot_id in {t.slot_id for t in tasks}:
        slot = await orders_api.slot_ref(session, slot_id)
        slots[slot_id] = (
            None
            if slot is None
            else PackingSlot(
                id=slot.id,
                label=slot.label,
                start=slot.start.strftime("%H:%M"),
                end=slot.end.strftime("%H:%M"),
            )
        )

    def flat_label(task: PackingTask) -> str | None:
        if task.flat is None:
            return None
        society = societies.get(task.society_id)
        tower = society.towers.get(task.tower_id or "") if society else None
        return f"{tower}, {task.flat}" if tower else task.flat

    out: list[PackingTaskOut] = []
    for task in tasks:
        society = societies.get(task.society_id)
        total, finished = counts.get(task.id, (0, 0))
        out.append(
            PackingTaskOut(
                id=task.id,
                hub_id=task.hub_id,
                order_id=task.order_id,
                order_kind=task.order_kind,  # pyright: ignore[reportArgumentType]
                delivery_date=task.delivery_date,
                slot=slots[task.slot_id],
                society=PackingSociety(id=task.society_id, name=society.name if society else ""),
                flat_label=flat_label(task),
                seq=task.seq,
                route=None
                if task.route_id is None or task.stop_seq is None
                else PackingRoute(route_id=task.route_id, stop_seq=task.stop_seq),
                status=task.status,  # pyright: ignore[reportArgumentType]
                packer=None
                if task.packer_user_id is None
                else Packer(user_id=task.packer_user_id, name=names.get(task.packer_user_id)),
                bag_qr=bag_qr(task.order_id),
                bags=task.bags,
                crate_no=task.crate_no,
                lines_total=total,
                lines_done=finished,
                started_at=task.started_at,
                packed_at=task.packed_at,
                version=task.version,
            )
        )
    return out


async def detail(session: AsyncSession, task: PackingTask, lang: Language) -> PackingTaskDetail:
    """The task with its lines, by catalog category, then name."""
    lines = await lines_of(session, task.id)
    wanted = {line.product_id for line in lines} | {
        line.substitute_product_id for line in lines if line.substitute_product_id
    }
    products = await catalog_api.line_products(session, list(wanted))

    def step(qty: int, unit: str) -> PackingStep:
        return PackingStep(qty=qty, unit=unit, label=catalog_api.step_label(qty, unit, lang))

    def shown(line: PackingLine) -> tuple[tuple[int, str], PackingLineOut]:
        product = products.get(line.product_id)
        substitute = (
            products.get(line.substitute_product_id) if line.substitute_product_id else None
        )
        name = localised(product.names, lang) if product else ""
        out = PackingLineOut(
            product_id=line.product_id,
            slug=product.slug if product else "",
            name=name,
            step=step(line.step_qty, line.step_unit),
            qty=line.qty,
            picked_qty=line.picked_qty,
            short_qty=line.short_qty,
            substitute=None
            if substitute is None
            else PackingSubstitute(
                product_id=substitute.id,
                slug=substitute.slug,
                name=localised(substitute.names, lang),
                step=step(substitute.step_qty, substitute.step_unit),
            ),
            done=line.picked_qty + line.short_qty == line.qty,
        )
        return (product.shelf if product else (1 << 30, "")), out

    ordered = sorted((shown(line) for line in lines), key=lambda x: (x[0], x[1].name.casefold()))
    (view,) = await views(session, [task])
    return PackingTaskDetail(**view.model_dump(), lines=[line for _, line in ordered])


async def get_task(
    session: AsyncSession, task_id: uuid.UUID, permit: Permit, *, for_update: bool = False
) -> PackingTask:
    """The task, or 404 `hub_ops.task_not_found` (also for another hub's)."""
    stmt = select(PackingTask).where(PackingTask.id == task_id)
    if for_update:
        stmt = stmt.with_for_update()
    task = (await session.execute(stmt)).scalar_one_or_none()
    if task is None or not permit.allows(task.hub_id):
        raise task_not_found()
    return task


async def bag_for_order(session: AsyncSession, order_id: uuid.UUID) -> PackingTask | None:
    return (
        await session.execute(select(PackingTask).where(PackingTask.order_id == order_id))
    ).scalar_one_or_none()
