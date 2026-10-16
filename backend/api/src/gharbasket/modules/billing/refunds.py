"""Refund to source (spec §3.3, ADR-0004): unused wallet money goes back only to the payments it
came from.

- The request posts the ledger at once, one `refund_to_source` entry per part (wallet →
  razorpay_clearing), and queues the parts. It never calls Razorpay: `send_refunds` does.
- Parts are split over the user's paid top-ups, newest first, within Razorpay's refund window.
- A part in flight (pending or sent) holds its room under the ₹10,000 cap, so when Razorpay
  fails it the money always fits back into the wallet (`refund_to_source_failed`).
- `send_refunds` sends the parts to Razorpay, and its webhooks settle them.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service, wallet
from gharbasket.modules.billing.models import (
    LedgerAccount,
    Payment,
    Refund,
    RefundPart,
)
from gharbasket.modules.billing.schemas import RefundOut, RefundPartOut
from gharbasket.modules.billing.service import Leg
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor
from gharbasket.platform.settings import Settings

MIN_PAISE = 100  # ₹1, Razorpay's smallest refund
DAILY_LIMIT = 3  # requests per IST day
SETTLED = ("processed", "partly_failed", "failed")  # a refund's final states


def refund_not_found() -> ProblemError:
    return ProblemError(404, "billing.refund_not_found", "No such refund.")


def _too_small(refundable: int) -> ProblemError:
    return ProblemError(
        422,
        "billing.refund_too_small",
        "Refund at least ₹1.",
        extra={"refundable_paise": refundable},
    )


@dataclass(frozen=True)
class Room:
    payment: Payment
    room_paise: int  # what this top-up can still take back


async def rooms(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime
) -> list[Room]:
    """The top-ups that can still take money back, newest first. A top-up's `created_at` (its
    order) is never later than its capture, so the window is never stretched."""
    taken = (
        select(RefundPart.payment_id, func.sum(RefundPart.amount_paise).label("paise"))
        .where(RefundPart.status != "failed")
        .group_by(RefundPart.payment_id)
        .subquery()
    )
    providers = ("razorpay", "dev") if settings.is_local else ("razorpay",)
    rows = await session.execute(
        select(Payment, func.coalesce(taken.c.paise, 0))
        .outerjoin(taken, taken.c.payment_id == Payment.id)
        .where(
            Payment.user_id == user_id,
            Payment.status == "paid",
            Payment.provider.in_(providers),
            Payment.provider_payment_id.is_not(None),
            Payment.created_at >= now - timedelta(days=settings.razorpay_refund_window_days),
        )
        .order_by(Payment.created_at.desc(), Payment.id.desc())
    )
    return [Room(p, p.amount_paise - int(t)) for p, t in rows.all() if p.amount_paise > t]


async def refundable(
    session: AsyncSession, user_id: uuid.UUID, settings: Settings, now: datetime | None = None
) -> int:
    found = await rooms(session, user_id, settings, now or datetime.now(UTC))
    return min(await wallet.balance(session, user_id), sum(r.room_paise for r in found))


async def wallet_account(session: AsyncSession, user_id: uuid.UUID) -> LedgerAccount:
    return await service.owned_account(
        session, "wallet", "user", user_id, kind="liability", lock=True
    )


async def request_refund(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount_paise: int | None,
    *,
    settings: Settings,
    idempotency_key: str,
    now: datetime | None = None,
) -> Refund:
    """POST /v1/wallet/refund-to-source: `None` sends back everything refundable."""
    now = now or datetime.now(UTC)
    account = await wallet_account(session, user_id)  # requests of one user run one at a time
    open_refund = (
        await session.execute(
            select(Refund.id).where(Refund.user_id == user_id, Refund.status.not_in(SETTLED))
        )
    ).scalar()
    if open_refund is not None:
        raise ProblemError(
            409,
            "billing.refund_in_progress",
            "An earlier refund is still on its way.",
            extra={"refund_id": str(open_refund)},
        )
    day_start = now.astimezone(wallet.IST).replace(hour=0, minute=0, second=0, microsecond=0)
    today = (
        await session.execute(
            select(func.count()).where(
                Refund.user_id == user_id,
                Refund.reason == "customer",
                Refund.created_at >= day_start,
            )
        )
    ).scalar_one()
    if today >= DAILY_LIMIT:
        raise ProblemError(
            429, "billing.too_many_refunds", "At most 3 refunds a day. Try again tomorrow."
        )
    found = await rooms(session, user_id, settings, now)
    can = min(await service.balance(session, account), sum(r.room_paise for r in found))
    amount = can if amount_paise is None else amount_paise
    if amount < MIN_PAISE:
        raise _too_small(can)
    if amount > can:
        raise ProblemError(
            422,
            "billing.refund_exceeds_refundable",
            "That is more than can go back to your payments.",
            extra={"refundable_paise": can},
        )
    refund = await _create(session, account, user_id, amount, found, "customer", idempotency_key)
    await audit.record(
        session,
        actor_user_id=user_id,
        action="billing.refund_requested",
        entity_type="refund",
        entity_id=str(refund.id),
        after={"amount_paise": amount, "reason": "customer"},
    )
    return refund


async def _create(
    session: AsyncSession,
    account: LedgerAccount,
    user_id: uuid.UUID,
    amount: int,
    found: list[Room],
    reason: str,
    key: str | None,
) -> Refund:
    refund = Refund(
        user_id=user_id, amount_paise=amount, reason=reason, status="pending", idempotency_key=key
    )
    session.add(refund)
    await session.flush()
    left = amount
    for room in found:  # newest first; one part per top-up
        take = min(room.room_paise, left)
        if take > 0:
            await add_part(session, account, refund, room.payment, take)
            left -= take
    if left:
        raise ValueError("the refund is larger than its top-ups can take")
    await session.refresh(refund)
    return refund


async def add_part(
    session: AsyncSession,
    account: LedgerAccount,
    refund: Refund,
    payment: Payment,
    amount: int,
    replaces: uuid.UUID | None = None,
) -> RefundPart:
    """One part and its `refund_to_source` entry (wallet → razorpay_clearing), keyed by the part."""
    part_id = (await session.execute(select(func.uuidv7()))).scalar_one()
    clearing = await service.platform_account(session, "razorpay_clearing")
    entry, _ = await service.post(
        session,
        kind="refund_to_source",
        key=f"rts:{part_id}",
        legs=[Leg(account, amount), Leg(clearing, -amount)],
        ref_type="refund",
        ref_id=refund.id,
        details={"part_id": str(part_id), "topup_id": str(payment.id)},
    )
    part = RefundPart(
        id=part_id,
        refund_id=refund.id,
        payment_id=payment.id,
        replaces_part_id=replaces,
        amount_paise=amount,
        status="pending",
        entry_id=entry.id,
    )
    session.add(part)
    await session.flush()
    return part


async def post_unclaimed(
    session: AsyncSession,
    account: LedgerAccount,
    amount: int,
    key: str,
    refund_id: uuid.UUID | None,
) -> None:
    """Money no payment can take back: a platform liability, never revenue (spec §3.3)."""
    unclaimed = await service.platform_account(session, "unclaimed_balances")
    await service.post(
        session,
        kind="unclaimed",
        key=key,
        legs=[Leg(account, amount), Leg(unclaimed, -amount)],
        ref_type="refund" if refund_id else None,
        ref_id=refund_id,
    )


async def close_wallet(
    session: AsyncSession, user_id: uuid.UUID, reason: str, settings: Settings
) -> Refund | None:
    """Account deletion (M1 §3.5): the refundable balance becomes one refund, the rest goes to
    `unclaimed_balances`. Not limited by the one-refund or three-a-day rules."""
    account = await wallet_account(session, user_id)
    balance = await service.balance(session, account)
    if balance == 0:
        return None
    found = await rooms(session, user_id, settings, datetime.now(UTC))
    amount = min(balance, sum(r.room_paise for r in found))
    refund = None
    if amount >= MIN_PAISE:
        refund = await _create(session, account, user_id, amount, found, reason, None)
    rest = balance - (refund.amount_paise if refund else 0)
    if rest:
        await post_unclaimed(session, account, rest, f"unclaimed:{user_id}", None)
    await audit.record(
        session,
        actor_user_id=None,
        action="billing.wallet_closed",
        entity_type="wallet",
        entity_id=str(user_id),
        after={"refund_id": str(refund.id) if refund else None, "unclaimed_paise": rest},
    )
    return refund


async def wallet_closed(session: AsyncSession, user_id: uuid.UUID) -> bool:
    return (
        await wallet.balance(session, user_id) == 0
        and await wallet.in_flight_paise(session, user_id) == 0
    )


# --- reading -----------------------------------------------------------------------------------


async def get_refund(session: AsyncSession, user_id: uuid.UUID, refund_id: uuid.UUID) -> Refund:
    refund = await session.get(Refund, refund_id)
    if refund is None or refund.user_id != user_id:  # someone else's looks like no refund
        raise refund_not_found()
    return refund


async def list_refunds(
    session: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[Refund], str | None]:
    """Newest first (uuidv7 ids)."""
    stmt = select(Refund).where(Refund.user_id == user_id)
    if cursor is not None:
        try:
            before = uuid.UUID(str(decode_cursor(cursor).get("before")))
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(Refund.id < before)
    found = list(
        (await session.execute(stmt.order_by(Refund.id.desc()).limit(limit + 1))).scalars()
    )
    more = len(found) > limit
    found = found[:limit]
    return found, encode_cursor({"before": str(found[-1].id)}) if more else None


async def to_out(session: AsyncSession, refund: Refund) -> RefundOut:
    rows = await session.execute(
        select(RefundPart, Payment.provider_payment_id)
        .join(Payment, Payment.id == RefundPart.payment_id)
        .where(RefundPart.refund_id == refund.id)
        .order_by(RefundPart.id)
    )
    return RefundOut(
        id=refund.id,
        amount_paise=refund.amount_paise,
        reason=refund.reason,  # pyright: ignore[reportArgumentType]
        status=refund.status,  # pyright: ignore[reportArgumentType]
        parts=[
            RefundPartOut(
                topup_id=part.payment_id,
                razorpay_payment_id=payment_ref or "",
                amount_paise=part.amount_paise,
                status=part.status,  # pyright: ignore[reportArgumentType]
                razorpay_refund_id=part.provider_refund_id,
            )
            for part, payment_ref in rows.all()
        ],
        created_at=refund.created_at,
        settled_at=refund.settled_at,
    )
