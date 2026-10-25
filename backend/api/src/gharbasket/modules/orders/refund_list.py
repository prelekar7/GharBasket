"""The order refunds list (spec §3.8, M5 §3.8): the money that went back for a hub's orders, read
only. M4 joins its own orders to `billing.api.order_refunds`; M5 knows no order or household.
Nothing moves here and nothing is stored: a refund is posted when the shortage, issue credit or
move to `refunded` happens (M5 §3.3)."""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders.models import Order
from gharbasket.modules.orders.schemas import RefundRow, RefundSociety
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor

DEFAULT_DAYS = 7
MAX_DAYS = 31
CHUNK = 2_000  # orders to a facade call
PREFIXES = {"short_": "shortage", "issue_": "issue", "carry_": "carry_over"}
KEPT_NAMES = {"undelivered", "no_kitchen"}


def window(start: date | None, end: date | None, today: date) -> tuple[date, date]:
    """The delivery dates asked for (default the last 7 days to today), at most 31 days apart."""
    end = end or today
    start = start or end - timedelta(days=DEFAULT_DAYS)
    if start > end or (end - start).days > MAX_DAYS:
        raise ProblemError(
            422, "request.invalid", f"Ask for from and to at most {MAX_DAYS} days apart."
        )
    return start, end


def classify(code: str) -> tuple[str, uuid.UUID | None]:
    """The reason an entry's code stands for and the id in its hex part (a product for a shortage
    or a carry-over, an issue for an issue credit); any other code is `other`."""
    if code in KEPT_NAMES:
        return code, None
    for prefix, reason in PREFIXES.items():
        hexed = code.removeprefix(prefix)
        if code.startswith(prefix) and len(hexed) == 32:
            try:
                return reason, uuid.UUID(hex=hexed)
            except ValueError:
                break
    return "other", None


def _position(cursor: str) -> tuple[datetime, uuid.UUID]:
    after = decode_cursor(cursor).get("after")
    try:
        if not (isinstance(after, list) and len(after) == 2):  # pyright: ignore[reportUnknownArgumentType]
            raise ValueError
        return datetime.fromisoformat(str(after[0])), uuid.UUID(str(after[1]))  # pyright: ignore[reportUnknownArgumentType]
    except ValueError as exc:
        raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc


async def page(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID | None,
    start: date,
    end: date,
    reason: str | None,
    destination: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[RefundRow], str | None]:
    """Refunds of the orders delivered from `start` to `end` at the hub (every hub when None),
    newest first by (created_at, id)."""
    after = _position(cursor) if cursor is not None else None
    stmt = select(Order).where(Order.delivery_date >= start, Order.delivery_date <= end)
    if hub_id is not None:
        stmt = stmt.where(Order.hub_id == hub_id)
    orders = {o.id: o for o in await session.scalars(stmt)}
    ids = list(orders)
    found: list[billing_api.OrderRefund] = []
    for i in range(0, len(ids), CHUNK):
        found += await billing_api.order_refunds(session, ids[i : i + CHUNK])
    found.sort(key=lambda r: (r.created_at, r.entry_id), reverse=True)

    wanted: list[tuple[billing_api.OrderRefund, str, uuid.UUID | None]] = []
    for refund in found:
        kind, hexed = classify(refund.reason)
        if reason is not None and kind != reason:
            continue
        if destination is not None and (refund.refund_id is None) != (destination == "wallet"):
            continue
        if after is not None and (refund.created_at, refund.entry_id) >= after:
            continue
        wanted.append((refund, kind, hexed))
    shown = wanted[:limit]
    next_cursor = None
    if len(wanted) > limit:
        last = shown[-1][0]
        next_cursor = encode_cursor({"after": [last.created_at.isoformat(), str(last.entry_id)]})

    chosen = [orders[r.order_id] for r, _, _ in shown]
    societies = await community_api.society_labels(session, list({o.society_id for o in chosen}))
    labels = await identity_api.delivery_labels(session, {o.household_id for o in chosen})
    rows: list[RefundRow] = []
    for refund, kind, hexed in shown:
        order = orders[refund.order_id]
        society = societies.get(order.society_id)
        label = labels.get(order.household_id)
        tower = (
            (society.towers.get(label.tower_id, label.tower_id) if society else "") if label else ""
        )
        rows.append(
            RefundRow(
                id=refund.entry_id,
                order_id=order.id,
                order_kind=order.kind,  # pyright: ignore[reportArgumentType]
                delivery_date=order.delivery_date,
                society=RefundSociety(id=order.society_id, name=society.name if society else ""),
                flat_label=None if label is None else f"{tower}, {label.flat}",
                reason=kind,  # pyright: ignore[reportArgumentType]
                product_id=hexed if kind in ("shortage", "carry_over") else None,
                issue_id=hexed if kind == "issue" else None,
                amount_paise=refund.amount_paise,
                to="wallet" if refund.refund_id is None else "source",
                refund_id=refund.refund_id,
                status="credited" if refund.refund_status is None else refund.refund_status,  # pyright: ignore[reportArgumentType]
                created_at=refund.created_at,
            )
        )
    return rows, next_cursor
