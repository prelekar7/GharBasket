"""Revenue recognition on delivery (spec §3.9): an order's money moves from unearned_revenue to
sales_revenue when it is delivered, and a refund after that comes out of sales_revenue.

- What an order holds in unearned_revenue is the sum of its entries' postings there (`ref` the
  order): its `box_debit` less the refunds already taken from it.
- `recognise` posts a `revenue` entry for what the order holds (a carried item: for its part of
  the missed order), once per key.
- `refund_legs` takes a refund from unearned_revenue up to what the order holds, and from
  sales_revenue for the rest up to what was recognised for the order (less its earlier refunds
  from there); more is refused, so per order neither account ever goes below zero.
- `recognise_closed` does the same for a refunded order's kept money, on M4's `orders.closed`.
- Both take the order's transaction lock before reading what it holds, so a refund and the
  recognition at the same moment never both take the same money. Lock order: M4's order row,
  then this lock, then the wallet's row.
"""

import hashlib
import uuid
from collections.abc import Iterable
from typing import Any, cast

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.errors import ProblemError

log = structlog.get_logger("gharbasket.billing.revenue")

RETAINED = "retained"  # `details.reason` of a refunded order's kept money (§3.9)


def _lock_key(order_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"gharbasket:billing:order:{order_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def exceeds_paid(order_id: uuid.UUID, refundable_paise: int) -> ProblemError:
    return ProblemError(
        422,
        "billing.order_refund_exceeds_paid",
        "This refund is more than is left of what the order paid.",
        extra={"order_id": str(order_id), "refundable_paise": refundable_paise},
    )


async def lock_order(session: AsyncSession, order_id: uuid.UUID) -> None:
    """Hold the order's money until the transaction ends. A lock taken inside a savepoint that
    is rolled back is released with it."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _lock_key(order_id)})


async def _by_order(
    session: AsyncSession, account: LedgerAccount, order_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Each order's balance on a credit-normal platform account (its entries, `ref` the order)."""
    ids = {i for i in order_ids if isinstance(i, uuid.UUID)}
    if not ids:
        return {}
    rows = await session.execute(
        select(JournalEntry.ref_id, func.sum(Posting.amount))
        .join(Posting, Posting.entry_id == JournalEntry.id)
        .where(
            Posting.account_id == account.id,
            JournalEntry.ref_type == "order",
            JournalEntry.ref_id.in_(ids),
        )
        .group_by(JournalEntry.ref_id)
    )
    found = {ref: -int(total) for ref, total in rows.all() if ref is not None}
    return {i: found.get(i, 0) for i in ids}


async def held_by_order(
    session: AsyncSession, order_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """What each order holds in unearned_revenue now (0 for an order M5 does not know)."""
    unearned = await service.platform_account(session, "unearned_revenue")
    return await _by_order(session, unearned, order_ids)


async def held_paise(session: AsyncSession, order_id: uuid.UUID) -> int:
    return (await held_by_order(session, [order_id]))[order_id]


async def sold_paise(session: AsyncSession, order_id: uuid.UUID) -> int:
    """What the order has in sales_revenue: recognised, less refunds taken from there."""
    sales = await service.platform_account(session, "sales_revenue")
    return (await _by_order(session, sales, [order_id]))[order_id]


async def refund_legs(session: AsyncSession, order_id: uuid.UUID, amount_paise: int) -> list[Leg]:
    """The debit legs of a refund of the order: unearned_revenue first, then sales_revenue up to
    what the order has there. More than both is `billing.order_refund_exceeds_paid`."""
    await lock_order(session, order_id)
    held = max(0, await held_paise(session, order_id))
    sold = max(0, await sold_paise(session, order_id))
    if amount_paise > held + sold:
        raise exceeds_paid(order_id, held + sold)
    unearned = min(amount_paise, held)
    legs: list[Leg] = []
    if unearned:
        legs.append(Leg(await service.platform_account(session, "unearned_revenue"), unearned))
    if amount_paise - unearned:
        sales = await service.platform_account(session, "sales_revenue")
        legs.append(Leg(sales, amount_paise - unearned))
    return legs


async def replayed_refund(
    session: AsyncSession, entry: JournalEntry, amount_paise: int
) -> JournalEntry:
    """A refund's key posted again: the recorded entry if it is the same refund, else a 409. Its
    legs depend on what the order held then, so only the kind and the amount are compared."""
    debited = await session.scalar(
        select(func.coalesce(func.sum(Posting.amount), 0)).where(
            Posting.entry_id == entry.id, Posting.amount > 0
        )
    )
    if entry.kind != "refund" or int(debited or 0) != amount_paise:
        raise service.entry_conflict(entry.idempotency_key)
    return entry


async def recognise(
    session: AsyncSession,
    order_id: uuid.UUID,
    key: str,
    *,
    most_paise: int | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Recognise what the order holds (at most `most_paise`) under `key`, once: the paise posted
    now (0 for a key already posted, or an order that holds nothing)."""
    await lock_order(session, order_id)
    if await service.find_entry(session, key) is not None:
        return 0
    held = await held_paise(session, order_id)
    amount = held if most_paise is None else min(most_paise, held)
    if amount <= 0:
        return 0
    unearned = await service.platform_account(session, "unearned_revenue")
    sales = await service.platform_account(session, "sales_revenue")
    await service.post(
        session,
        kind="revenue",
        key=key,
        legs=[Leg(unearned, amount), Leg(sales, -amount)],
        ref_type="order",
        ref_id=order_id,
        details=details,
    )
    return amount


def _dict(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def recognise_delivery(session: AsyncSession, body: object, event_id: uuid.UUID) -> int:
    """`orders.delivered`: the order's revenue, then each carried item's on its missed order.
    Returns the paise newly recognised. What it cannot read is logged and skipped."""
    payload = _dict(body)
    order_id = _uuid(payload.get("order_id"))
    if order_id is None:
        log.warning("delivered_unusable", event_id=str(event_id))
        return 0
    details = {k: str(payload[k]) for k in ("delivery_date", "kind") if payload.get(k) is not None}
    paise = await recognise(session, order_id, f"revenue:{order_id}", details=details)
    carried: object = payload.get("carried")
    for item in cast(list[object], carried) if isinstance(carried, list) else []:
        row = _dict(item)
        missed, product = _uuid(row.get("from_order_id")), _uuid(row.get("product_id"))
        paid = row.get("paid_paise")
        if missed is None or product is None or type(paid) is not int or paid < 0:
            log.warning("delivered_carry_unusable", event_id=str(event_id))
            continue
        if paid == 0:
            continue
        paise += await recognise(
            session,
            missed,
            f"revenue:{missed}:carry_{product.hex}",
            most_paise=paid,
            details={
                "delivered_with": str(order_id),
                **({"carry_id": str(row["carry_id"])} if row.get("carry_id") else {}),
            },
        )
    return paise


async def recognise_closed(session: AsyncSession, body: object, event_id: uuid.UUID) -> int:
    """`orders.closed`: what a `refunded` order still holds (the second-miss fee, the rest of a
    partial refund) is recognised under `revenue:<order id>`, reason `retained`. A refunded order
    is never delivered, so the key is free; an order that holds nothing, or a repeat, posts
    nothing. Returns the paise newly recognised; a payload without an order id is logged."""
    order_id = _uuid(_dict(body).get("order_id"))
    if order_id is None:
        log.warning("closed_unusable", event_id=str(event_id))
        return 0
    return await recognise(session, order_id, f"revenue:{order_id}", details={"reason": RETAINED})
