"""Packing (spec §3.2, ADR-0026 §6): tasks built at T 05:00 in delivery order, and the packer's
start, pick, shortage and complete.

The order belongs to M4: every move goes through `orders.api` (`transition` with actor `packer`,
or `hub` for a hub manager, and `mark_shortage`) in the request's transaction, so an order M4
refuses to move (409 `orders.illegal_transition`) leaves the task unchanged. A task is row-locked
while it changes; `next` skips tasks another packer is claiming (`FOR UPDATE SKIP LOCKED`).
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import procurement, receiving, service
from gharbasket.modules.hub_ops.models import KeptItem, PackingEvent, PackingLine, PackingTask
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
CRATE_BAGS = 3  # bags to a crate (ADR-0026 §6: crates are counted, not tracked)
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


@dataclass(frozen=True)
class Actor:
    """Who acts on a task: a packer, or a hub manager (actor `hub` on the order)."""

    user_id: uuid.UUID
    manager: bool

    @property
    def order_actor(self) -> str:
        return "hub" if self.manager else "packer"


def actor(permit: Permit, hub_id: uuid.UUID) -> Actor:
    manager = any(
        g.role in ("hub_manager", "admin") and g.scope_id in (None, hub_id) for g in permit.grants
    )
    return Actor(permit.principal.user_id, manager)


async def household_allows_substitutes(session: AsyncSession, household_id: uuid.UUID) -> bool:
    """False for a household that chose "never substitute, refund" (ADR-0026 §6, M1 §3.4), read
    when the shortage is recorded so a change applies at once; M4's `mark_shortage` checks again."""
    preferences = await identity_api.shortage_preferences(session, [household_id])
    return preferences[household_id] != "refund"


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
    re-sequence queued tasks; `route_id` and `stop_seq` stay null until then. The kept items M4
    attached to an order (`OrderSummary.carried`, §3.5) are lines of their own with `carried`;
    a queued task built earlier takes the ones attached since (or loses a refunded one)."""
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
    queued = {
        task.order_id: task
        for task in await session.scalars(
            select(PackingTask).where(
                PackingTask.hub_id == hub_id,
                PackingTask.delivery_date == day,
                PackingTask.status == "queued",
            )
        )
    }
    for summary in found:
        if (task := queued.get(summary.order.id)) is not None:
            await sync_carried(session, task, summary.carried)
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
        await sync_carried(session, task, summary.carried)
    await session.flush()
    return len(new), len(existing)


def _carried_wanted(
    carried: list[orders_api.CarriedLine],
) -> dict[uuid.UUID, tuple[int, int, str]]:
    """Carried items by product: (steps, step_qty, step_unit); two items of one product, from
    two missed boxes, share the one carried line."""
    wanted: dict[uuid.UUID, tuple[int, int, str]] = {}
    for item in carried:
        steps = wanted.get(item.product_id, (0, item.step_qty, item.step_unit))[0]
        wanted[item.product_id] = (steps + item.steps, item.step_qty, item.step_unit)
    return wanted


async def sync_carried(
    session: AsyncSession, task: PackingTask, carried: list[orders_api.CarriedLine]
) -> bool:
    """Make the task's carried lines the order's `carried` items: add the missing ones, follow a
    change of steps, drop one M4 no longer carries (the hub lost it). True if anything changed."""
    wanted = _carried_wanted(carried)
    have = {line.product_id: line for line in await lines_of(session, task.id) if line.carried}
    changed = False
    for product_id, (steps, step_qty, step_unit) in wanted.items():
        line = have.pop(product_id, None)
        if line is None:
            session.add(
                PackingLine(
                    task_id=task.id,
                    product_id=product_id,
                    carried=True,
                    step_qty=step_qty,
                    step_unit=step_unit,
                    qty=steps,
                )
            )
            changed = True
        elif line.qty != steps:
            line.qty, line.picked_qty = steps, min(line.picked_qty, steps)
            changed = True
    for gone in have.values():
        await session.delete(gone)
        changed = True
    if changed:
        task.version += 1
        await session.flush()
    return changed


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
            carried=line.carried,
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

    ordered = sorted(
        (shown(line) for line in lines),
        key=lambda x: (x[0], x[1].name.casefold(), x[1].carried),
    )
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


# --- the packer's moves ---------------------------------------------------------------------------


def _changed(
    session: AsyncSession,
    task: PackingTask,
    action: str,
    by: Actor,
    payload: dict[str, Any] | None,
    client_at: datetime | None,
) -> None:
    task.version += 1
    session.add(
        PackingEvent(
            task_id=task.id, action=action, user_id=by.user_id, payload=payload, client_at=client_at
        )
    )


def _held(task: PackingTask, by: Actor) -> None:
    """Pick, shortage and complete need a started task held by the caller (or a hub manager)."""
    if task.status == "packed":
        raise _problem(409, "hub_ops.task_packed", "This task is packed.")
    if task.status == "queued":
        raise _problem(409, "hub_ops.task_not_started", "Start the task first.")
    if not by.manager and task.packer_user_id != by.user_id:
        raise _problem(403, "hub_ops.not_your_task", "Another packer holds this task.")


async def _line(
    session: AsyncSession, task: PackingTask, product_id: uuid.UUID, carried: bool = False
) -> PackingLine:
    """The line of the ordered product, or of its substitute. The paid line comes first; the
    kept item's own line is meant by `carried`, or when the product has no paid line."""
    found = [
        line
        for line in await lines_of(session, task.id)
        if product_id in (line.product_id, line.substitute_product_id)
    ]
    own = [line for line in found if line.carried == carried]
    if own or (not carried and found):
        return (own or found)[0]
    raise _problem(404, "hub_ops.line_not_found", "This product is not on the task.")


async def start(
    session: AsyncSession,
    task: PackingTask,
    by: Actor,
    scanned: str | None = None,
    client_at: datetime | None = None,
) -> None:
    """Take a queued task (the order moves locked → picking) or a released one."""
    if task.status == "packed":
        raise _problem(409, "hub_ops.task_packed", "This task is packed.")
    if scanned is not None and scanned != bag_qr(task.order_id):
        raise _problem(422, "hub_ops.bag_mismatch", "This bag belongs to another order.")
    if task.packer_user_id == by.user_id:
        return
    if task.packer_user_id is not None:
        raise _problem(409, "hub_ops.task_taken", "Another packer holds this task.")
    if task.status == "queued":
        await orders_api.transition(
            session, task.order_id, "picking", actor=by.order_actor, actor_user_id=by.user_id
        )
        task.status, task.started_at = "picking", datetime.now(UTC)
    task.packer_user_id = by.user_id
    _changed(session, task, "start", by, {"bag_qr": scanned} if scanned else None, client_at)
    await session.flush()


async def claim_next(
    session: AsyncSession, hub_id: uuid.UUID, by: Actor, day: date
) -> PackingTask | None:
    """The caller's task in picking, else the first queued (or released) task by seq, started for
    them. Tasks whose order M4 will not move (packed in the console, say) are passed over."""
    mine = await session.scalar(
        select(PackingTask)
        .where(
            PackingTask.hub_id == hub_id,
            PackingTask.delivery_date == day,
            PackingTask.status == "picking",
            PackingTask.packer_user_id == by.user_id,
        )
        .order_by(PackingTask.seq)
        .limit(1)
    )
    if mine is not None:
        return mine
    passed: list[uuid.UUID] = []
    while True:
        task = await session.scalar(
            select(PackingTask)
            .where(
                PackingTask.hub_id == hub_id,
                PackingTask.delivery_date == day,
                or_(
                    PackingTask.status == "queued",
                    and_(PackingTask.status == "picking", PackingTask.packer_user_id.is_(None)),
                ),
                PackingTask.id.not_in(passed),
            )
            .order_by(PackingTask.seq)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if task is None:
            return None
        try:
            async with session.begin_nested():
                await start(session, task, by)
        except ProblemError as refused:
            if refused.code != "orders.illegal_transition":
                raise
            await session.refresh(task)
            passed.append(task.id)
            continue
        return task


async def pick(
    session: AsyncSession,
    task: PackingTask,
    by: Actor,
    product_id: uuid.UUID,
    picked_qty: int,
    client_at: datetime | None = None,
    carried: bool = False,
) -> None:
    """Set the steps in the bag (absolute, so a repeat changes nothing)."""
    _held(task, by)
    line = await _line(session, task, product_id, carried)
    if not 0 <= picked_qty <= line.qty - line.short_qty:
        raise _problem(
            422, "hub_ops.qty_invalid", "Picked steps are from 0 to the line's quantity less short."
        )
    if line.picked_qty == picked_qty:
        return
    line.picked_qty = picked_qty
    payload: dict[str, Any] = {"product_id": str(product_id), "picked_qty": picked_qty}
    if line.carried:
        payload["carried"] = True
    _changed(session, task, "pick", by, payload, client_at)
    await session.flush()


def _substitute_invalid(reason: str) -> ProblemError:
    return _problem(
        422, "hub_ops.substitute_invalid", "This product cannot replace the line.", reason=reason
    )


async def shortage(
    session: AsyncSession,
    task: PackingTask,
    by: Actor,
    product_id: uuid.UUID,
    short_qty: int,
    substitute_id: uuid.UUID | None,
    client_at: datetime | None = None,
    carried: bool = False,
) -> None:
    """Refund short steps, or replace the whole line with a substitute, through
    `orders.api.mark_shortage` (which checks the substitute list and price, and refunds). A
    carried line takes no shortage: nothing was paid for it (422 `hub_ops.qty_invalid`,
    `reason: "carried"`); the hub manager reports a kept item lost instead (§3.5)."""
    _held(task, by)
    line = await _line(session, task, product_id, carried)
    if line.carried:
        raise _problem(
            422,
            "hub_ops.qty_invalid",
            "A kept item riding free takes no shortage; report it lost instead.",
            reason="carried",
        )
    if line.short_qty or line.substitute_product_id:
        raise _problem(409, "hub_ops.shortage_recorded", "This line already has its shortage.")
    if substitute_id is None:
        if not 1 <= short_qty <= line.qty - line.picked_qty:
            raise _problem(
                422, "hub_ops.qty_invalid", "Short steps are from 1 to what is not picked yet."
            )
    else:
        if short_qty != line.qty or line.picked_qty:
            raise _substitute_invalid("partial")
        order = await orders_api.get(session, task.order_id)
        if not await household_allows_substitutes(session, order.household_id):
            raise _substitute_invalid("refund_preferred")
    try:
        await orders_api.mark_shortage(
            session, task.order_id, line.product_id, short_qty, substitute_id
        )
    except ProblemError as refused:
        if refused.code == "orders.shortage_recorded":
            raise _problem(
                409, "hub_ops.shortage_recorded", "This line already has its shortage."
            ) from refused
        if refused.code in ("orders.substitute_invalid", "orders.product_unknown"):
            reason = (refused.extra or {}).get("reason", "unknown")
            raise _substitute_invalid(str(reason)) from refused
        raise
    if substitute_id is None:
        line.short_qty = short_qty
    else:
        line.substitute_product_id = substitute_id
    payload = {
        "product_id": str(line.product_id),
        "short_qty": short_qty,
        "substitute_product_id": None if substitute_id is None else str(substitute_id),
    }
    _changed(session, task, "shortage", by, payload, client_at)
    await session.flush()


async def complete(
    session: AsyncSession,
    task: PackingTask,
    by: Actor,
    bags: int | None,
    crate_no: int | None,
    client_at: datetime | None = None,
) -> None:
    """Every line done: the bag is packed (the order moves picking → packed). Without
    `crate_no` the bag goes into the society's current crate, three bags to a crate."""
    _held(task, by)
    lines = await lines_of(session, task.id)
    open_lines = [str(ln.product_id) for ln in lines if ln.picked_qty + ln.short_qty < ln.qty]
    if open_lines:
        raise _problem(
            409,
            "hub_ops.lines_open",
            "Pick or mark short every line first.",
            product_ids=open_lines,
        )
    if crate_no is None:
        packed_bags = await session.scalar(
            select(func.coalesce(func.sum(PackingTask.bags), 0)).where(
                PackingTask.hub_id == task.hub_id,
                PackingTask.delivery_date == task.delivery_date,
                PackingTask.society_id == task.society_id,
                PackingTask.status == "packed",
            )
        )
        crate_no = min(int(packed_bags or 0) // CRATE_BAGS + 1, 99)
    await orders_api.transition(
        session, task.order_id, "packed", actor=by.order_actor, actor_user_id=by.user_id
    )
    await draw_carried(session, task, by.user_id)
    task.bags = bags or task.bags
    task.crate_no, task.status, task.packed_at = crate_no, "packed", datetime.now(UTC)
    _changed(session, task, "complete", by, {"bags": task.bags, "crate_no": crate_no}, client_at)
    await session.flush()


async def draw_carried(session: AsyncSession, task: PackingTask, by_user_id: uuid.UUID) -> None:
    """The kept items on a packed bag leave the shelf (§3.5): the item of that carry becomes
    `packed` with the task, and a `pack` move for it (no lot) is written; with no kept item (the
    bag was never scanned) the move stands alone, unlotted. The 12:30 draw-down is not built yet;
    when it is, it draws paid lines only, as these moves are keyed by the carried item."""
    [summary] = await orders_api.summaries(session, [task.order_id])
    for item in summary.carried:
        kept = await session.scalar(
            select(KeptItem)
            .where(KeptItem.carry_id == item.carry_id, KeptItem.status == "kept")
            .with_for_update()
        )
        if kept is not None:
            kept.status, kept.packed_task_id = "packed", task.id
            kept.settled_at = datetime.now(UTC)
        await receiving.move(
            session,
            hub_id=task.hub_id,
            product_id=item.product_id,
            unit=procurement.OPS_UNIT[item.step_unit][0],
            kind="pack",
            ref_id=item.carry_id if kept is None else kept.id,
            qty=-procurement.to_ops(item.steps, item.step_qty, item.step_unit),
            kept_item_id=None if kept is None else kept.id,
            by_user_id=by_user_id,
        )
    await session.flush()


async def release(
    session: AsyncSession, task: PackingTask, by: Actor, client_at: datetime | None = None
) -> None:
    """A hub manager frees a started task: any packer of the hub may start it again; the order
    stays in picking."""
    if task.status == "packed":
        raise _problem(409, "hub_ops.task_packed", "This task is packed.")
    if task.status == "queued":
        raise _problem(409, "hub_ops.task_not_started", "Nobody holds this task.")
    if task.packer_user_id is None:
        return
    payload = {"packer_user_id": str(task.packer_user_id)}
    task.packer_user_id = None
    _changed(session, task, "release", by, payload, client_at)
    await session.flush()
