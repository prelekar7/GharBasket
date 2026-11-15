"""The cut-over from test money to live money (spec §3.10, ADR-0037), run once by hand.

- `prepare` pauses the synthetic households (M3) and counts the blockers: whatever was paid or
  promised with test money and could still move money after the close.
- `close` posts one `test_mode_closed` entry that brings every INR account to zero, once, and
  only with no blocker. Kindness points are not money and are left alone.
- `mixed` is the probe `billing.live_mode_mixed`: live money and test money in the same books.
"""

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, service
from gharbasket.modules.billing.models import (
    IN_FLIGHT,
    JournalEntry,
    LedgerAccount,
    Payment,
    Posting,
    ReconciliationItem,
    RefundPart,
)
from gharbasket.modules.billing.service import Leg
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

KEY = "test_mode_closed"  # the closing entry's kind and its idempotency key
UNCLOSED_AFTER = timedelta(hours=24)
# When this process loaded M5: the API's start, as near as the probe needs.
STARTED_AT = datetime.now(UTC)


def not_live() -> ProblemError:
    return ProblemError(
        409,
        "billing.cutover_not_live",
        "The cut-over runs only with RAZORPAY_MODE=live.",
    )


async def blockers(session: AsyncSession, now: datetime) -> dict[str, int]:
    """What still holds test money (spec §3.10), each a count; all zero before the close. Refund
    parts and reconciliation items of every mode count: a test part left pending is never sent
    once live, so it must be flushed in test mode first.

    `live_topups_open` (not in the spec's list): live top-ups credited before the close and not
    wholly refunded to source. The close would zero that real money too, so it waits."""
    refunded = (
        select(func.coalesce(func.sum(RefundPart.amount_paise), 0))
        .where(RefundPart.payment_id == Payment.id, RefundPart.status != "failed")
        .scalar_subquery()
    )

    async def count(*where: Any) -> int:
        return int(await session.scalar(select(func.count()).where(*where)) or 0)

    return {
        "open_paid_orders": await orders_api.open_paid_order_count(session, now),
        "open_carry_overs": await orders_api.open_carry_over_count(session),
        "refund_parts_in_flight": await count(RefundPart.status.in_(IN_FLIGHT)),
        "unpaid_test_topups": await count(
            Payment.mode == "test", Payment.status == "created", Payment.expires_at > now
        ),
        "open_recon_items": await count(ReconciliationItem.status == "open"),
        "live_topups_open": await count(
            Payment.mode == "live", Payment.status == "paid", Payment.amount_paise > refunded
        ),
    }


async def prepare(session: AsyncSession, now: datetime) -> dict[str, Any]:
    paused = await subscriptions_api.pause_synthetic_households(session, reason="live_cutover")
    return {**paused, "blockers": await blockers(session, now)}


def _summary(entry: JournalEntry, already_done: bool) -> dict[str, Any]:
    details = entry.details or {}
    return {
        "entry_id": str(entry.id),
        "accounts": len(details.get("accounts", [])),
        "total_paise": details.get("total_paise", 0),
        "already_done": already_done,
    }


async def close(session: AsyncSession, now: datetime) -> dict[str, Any]:
    """The closing entry, or the one already posted (`already_done`). Two runs at once are one:
    the job's advisory lock; and while it reads the balances and posts, no other posting can
    land (the table lock is held to the commit), so every balance it zeroes is the final one."""
    done = await service.find_entry(session, KEY)
    if done is not None:
        return _summary(done, True)
    await session.execute(text("LOCK TABLE billing.postings IN EXCLUSIVE MODE"))
    found = await blockers(session, now)
    if any(found.values()):
        raise ProblemError(
            409,
            "billing.cutover_blocked",
            "Test money is still in play; run billing_cutover_prepare and clear each blocker.",
            extra={"blockers": found},
        )
    total = func.sum(Posting.amount)
    rows = (
        await session.execute(
            select(LedgerAccount, total)
            .join(Posting, Posting.account_id == LedgerAccount.id)
            .where(LedgerAccount.currency == "INR")
            .group_by(LedgerAccount.id)
            .having(total != 0)
            .order_by(LedgerAccount.code, LedgerAccount.owner_type, LedgerAccount.owner_id)
        )
    ).all()
    legs = [Leg(account, -int(amount)) for account, amount in rows]
    # Every entry sums to zero, so zeroing every account does too: the leg to `test_funds` (the
    # spec's balancing leg) is zero in a sound ledger and is posted only if it is not.
    balancing = -sum(leg.amount for leg in legs)
    if balancing:
        legs.append(Leg(await service.platform_account(session, "test_funds"), balancing))
    if not legs:  # nothing to close: an entry needs two postings, so none is made
        return {"entry_id": None, "accounts": 0, "total_paise": 0, "already_done": False}
    closed = sum(leg.amount for leg in legs if leg.amount > 0)
    accounts = [
        {
            "code": leg.account.code,
            "owner_type": leg.account.owner_type,
            "owner_id": str(leg.account.owner_id) if leg.account.owner_id else None,
            "amount_paise": leg.amount,  # what this entry posted: debit +, credit -
        }
        for leg in legs
    ]
    details = {"accounts": accounts, "total_paise": closed}
    entry, _ = await service.post(
        session, kind=KEY, key=KEY, legs=legs, ref_type="cutover", details=details
    )
    by_code = Counter(leg.account.code for leg in legs)
    await audit.record(
        session,
        actor_user_id=None,
        action="billing.cutover_live",
        entity_type="journal_entry",
        entity_id=str(entry.id),
        after={"accounts": len(legs), "by_code": dict(sorted(by_code.items())), **details},
    )
    await publish(
        session,
        events.CUTOVER_COMPLETED,
        {"entry_id": str(entry.id), "accounts": len(legs), "total_paise": closed},
    )
    return _summary(entry, False)


async def _test_money(session: AsyncSession, before: datetime | None) -> bool:
    """Whether the INR entries made before `before` (all, when None) leave any account non-zero:
    there is test money to close. A database live from its start has none."""
    query = (
        select(Posting.account_id)
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(LedgerAccount.currency == "INR")
    )
    if before is not None:
        query = query.where(JournalEntry.created_at < before)
    query = query.group_by(Posting.account_id).having(func.sum(Posting.amount) != 0).limit(1)
    return (await session.execute(query)).first() is not None


async def mixed(
    session: AsyncSession, now: datetime, *, mode: str, started_at: datetime
) -> list[str]:
    """The probe's two cases (spec §3.10):
    - live, no closing entry, test money to close, and 24 hours since the API started in live
      mode or since the first live payment, whichever was first (a restart alone resets the
      first; the second is in the database);
    - a `topup` entry after the closing entry that credited a `test` payment.
    """
    closed = await service.find_entry(session, KEY)
    if closed is None:
        if mode != "live":
            return []
        first_live = await session.scalar(
            select(func.min(Payment.created_at)).where(Payment.mode == "live")
        )
        since = min(started_at, first_live) if first_live else started_at
        if now - since < UNCLOSED_AFTER or not await _test_money(session, first_live):
            return []
        return [
            "the API runs live and the test money is not closed after 24 h; run "
            "billing_cutover_prepare, then billing_cutover_live (docs/runbooks/live-cutover.md)"
        ]
    late = await session.scalar(
        select(func.count())
        .select_from(JournalEntry)
        .join(Payment, Payment.id == JournalEntry.ref_id)
        .where(
            JournalEntry.kind == "topup",
            JournalEntry.ref_type == "topup",
            JournalEntry.id > closed.id,  # uuidv7: after it
            Payment.mode == "test",
        )
    )
    return [f"{late} test-mode top-ups credited after the test money was closed"] if late else []
