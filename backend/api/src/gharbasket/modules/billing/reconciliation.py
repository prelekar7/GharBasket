"""Reconciliation with Razorpay (spec §3.4): `reconcile_payments`, hourly at :17.

- It reads Razorpay's payments and refunds created in the 72 hours before `scheduled_for`, plus
  the payments of top-ups credited in that window and of items still open. It never writes to
  Razorpay.
- Everything is read before anything is written. If Razorpay is unavailable, the run is `failed`
  and changes nothing.
- A lost webhook is healed through the webhook's own code (`topups.handle_event`,
  `settlement.handle_event`), keyed by its cause, so a heal never credits or settles twice.
- Anything else opens one item per (kind, Razorpay ref). Seeing it again only moves
  `last_seen_at`. An admin resolves it with a note, and resolving moves no money (a correction
  is a manual, audited `adjustment` entry until the pilot).
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, settlement, topups
from gharbasket.modules.billing.models import (
    IN_FLIGHT,
    JournalEntry,
    Payment,
    ReconciliationItem,
    ReconciliationRun,
    RefundPart,
)
from gharbasket.modules.billing.razorpay import RazorpayClient
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor
from gharbasket.platform.settings import Settings

WINDOW = timedelta(hours=72)
STUCK_AFTER = timedelta(days=7)
CAPTURED = ("captured", "refunded")  # "refunded": captured, then refunded in full
Seen = dict[str, dict[str, Any]]  # Razorpay's entities by id


async def _credited(session: AsyncSession, start: datetime) -> list[Payment]:
    """Razorpay top-ups credited since `start`, and those an open item says are not captured."""
    recent = select(JournalEntry.ref_id).where(
        JournalEntry.kind == "topup", JournalEntry.created_at >= start
    )
    still_open = select(ReconciliationItem.razorpay_ref).where(
        ReconciliationItem.status == "open", ReconciliationItem.kind == "credited_not_captured"
    )
    found = await session.execute(
        select(Payment).where(
            Payment.provider == "razorpay",
            Payment.status == "paid",
            or_(Payment.id.in_(recent), Payment.provider_payment_id.in_(still_open)),
        )
    )
    return list(found.scalars())


async def _read(
    session: AsyncSession, razorpay: RazorpayClient, start: datetime, end: datetime
) -> tuple[Seen, Seen, list[Payment]]:
    """Everything Razorpay says that this run compares, before anything is written."""
    payments = {str(p["id"]): p for p in await razorpay.created_between("payments", start, end)}
    refunds = {str(r["id"]): r for r in await razorpay.created_between("refunds", start, end)}
    credited = await _credited(session, start)
    retry = await session.execute(
        select(ReconciliationItem.razorpay_ref).where(
            ReconciliationItem.status == "open",
            ReconciliationItem.kind == "captured_not_credited",
        )
    )
    wanted = {p.provider_payment_id for p in credited} | set(retry.scalars())
    for ref in sorted(r for r in wanted if r is not None and r not in payments):
        found = await razorpay.payment(ref)
        if found is not None:
            payments[ref] = found
    sent = await session.execute(
        select(RefundPart.provider_refund_id, Payment.provider_payment_id)
        .join(Payment, Payment.id == RefundPart.payment_id)
        .where(RefundPart.status == "sent", Payment.provider == "razorpay")
    )
    for payment_ref in sorted({str(p) for r, p in sent.all() if r not in refunds and p}):
        refunds.update({str(r["id"]): r for r in await razorpay.refunds(payment_ref)})
    return payments, refunds, credited


class _Run:
    def __init__(self, session: AsyncSession, run: ReconciliationRun, settings: Settings) -> None:
        self.session, self.run, self.settings = session, run, settings
        self.kinds: set[str] = set()

    async def flag(self, kind: str, ref: str, *, healed: bool = False, **fields: Any) -> None:
        """Record a mismatch once: an open item for (kind, ref) is only seen again."""
        now = datetime.now(UTC)
        item = (
            await self.session.execute(
                select(ReconciliationItem).where(
                    ReconciliationItem.kind == kind,
                    ReconciliationItem.razorpay_ref == ref,
                    ReconciliationItem.status == "open",
                )
            )
        ).scalar_one_or_none()
        if healed:
            self.run.healed += 1
        if item is not None:
            item.last_seen_at = now
            item.status = "healed" if healed else item.status
            return
        status = "healed" if healed else "open"
        self.session.add(
            ReconciliationItem(
                run_id=self.run.id,
                kind=kind,
                status=status,
                razorpay_ref=ref,
                first_seen_at=now,
                last_seen_at=now,
                **fields,
            )
        )
        await self.session.flush()
        if not healed:
            self.run.opened += 1
            self.kinds.add(kind)

    async def payment(self, found: dict[str, Any]) -> None:
        """A captured Razorpay payment against our top-up for its order."""
        ref, amount = str(found["id"]), int(found.get("amount") or 0)
        order = found.get("order_id")
        topup = await topups.by_order(self.session, str(order)) if order else None
        if topup is None:
            return await self.flag("unknown_payment", ref, actual_paise=amount)
        about = {
            "topup_id": topup.id,
            "user_id": topup.user_id,
            "expected_paise": topup.amount_paise,
            "actual_paise": amount,
        }
        if amount != topup.amount_paise or found.get("currency") != "INR":
            return await self.flag("amount_mismatch", ref, **about)
        if topup.provider_payment_id == ref and topup.status in ("paid", "refunded"):
            return
        if topup.status in ("created", "failed"):  # the webhook was lost: settle it as it would
            event = {"event": "payment.captured", "payload": {"payment": {"entity": found}}}
            try:
                async with self.session.begin_nested():
                    # No client: a payment that no longer fits under the cap would need a
                    # refund, which this read-only job leaves to an admin (an open item).
                    await topups.handle_event(self.session, event, None)
            except ProblemError:
                return await self.flag("captured_not_credited", ref, **about)
            return await self.flag("captured_not_credited", ref, healed=True, **about)
        await self.flag("captured_not_credited", ref, **about)  # a second payment on one order

    async def refund(self, found: dict[str, Any], paid: Payment) -> None:
        """A Razorpay refund on one of our top-ups against our refund part for it."""
        ref, status = str(found["id"]), found.get("status")
        part = (
            await self.session.execute(
                select(RefundPart).where(RefundPart.provider_refund_id == ref)
            )
        ).scalar_one_or_none()
        if part is None and found.get("receipt"):
            try:
                part = await self.session.get(RefundPart, uuid.UUID(str(found["receipt"])))
            except ValueError:
                part = None
            # A second refund for a part that already has one is money gone twice: unknown.
            if part is not None and (
                part.payment_id != paid.id or part.provider_refund_id not in (None, ref)
            ):
                part = None
        about = {"topup_id": paid.id, "user_id": paid.user_id, "actual_paise": found.get("amount")}
        if part is None:
            if paid.provider_refund_id != ref:  # not a late over-cap refund either
                await self.flag("refund_unknown", ref, **about)
            return
        if part.status in IN_FLIGHT and status in settlement.FINAL:
            event = {"event": f"refund.{status}", "payload": {"refund": {"entity": found}}}
            await settlement.handle_event(self.session, event, self.settings)
            await self.flag(
                "refund_status_mismatch",
                ref,
                healed=True,
                refund_part_id=part.id,
                expected_paise=part.amount_paise,
                **about,
            )

    async def stuck(self, now: datetime) -> None:
        rows = await self.session.execute(
            select(RefundPart, Payment)
            .join(Payment, Payment.id == RefundPart.payment_id)
            .where(
                RefundPart.status.in_(IN_FLIGHT),
                Payment.provider == "razorpay",
                func.uuid_extract_timestamp(RefundPart.id) < now - STUCK_AFTER,  # uuidv7
            )
        )
        for part, paid in rows.all():
            await self.flag(
                "refund_stuck",
                part.provider_refund_id or str(paid.provider_payment_id),
                topup_id=paid.id,
                refund_part_id=part.id,
                user_id=paid.user_id,
                expected_paise=part.amount_paise,
            )

    async def compare(
        self, payments: Seen, refunds: Seen, credited: list[Payment], now: datetime
    ) -> None:
        for found in payments.values():
            if found.get("status") in CAPTURED:
                self.run.checked += 1
                await self.payment(found)
        for topup in credited:
            ref = str(topup.provider_payment_id)
            found = payments.get(ref)
            self.run.checked += 1
            if found is None or found.get("status") not in CAPTURED:
                await self.flag(
                    "credited_not_captured",
                    ref,
                    topup_id=topup.id,
                    user_id=topup.user_id,
                    expected_paise=topup.amount_paise,
                    actual_paise=0,
                )
        refs = {str(r.get("payment_id")) for r in refunds.values()}
        ours = await self.session.execute(
            select(Payment).where(Payment.provider_payment_id.in_(refs))
        )
        by_ref = {str(p.provider_payment_id): p for p in ours.scalars()}
        for found in refunds.values():
            paid = by_ref.get(str(found.get("payment_id")))
            if paid is not None:
                self.run.checked += 1
                await self.refund(found, paid)
        await self.stuck(now)


async def reconcile(
    session: AsyncSession, razorpay: RazorpayClient | None, settings: Settings, now: datetime
) -> dict[str, Any]:
    run = ReconciliationRun(
        scheduled_for=now,
        window_from=now - WINDOW,
        window_to=now,
        status="skipped",  # without keys (dev top-ups only) there is nothing to compare
        checked=0,
        healed=0,
        opened=0,
    )
    session.add(run)
    payments: Seen = {}
    refunds: Seen = {}
    credited: list[Payment] = []
    if razorpay is not None:
        try:
            payments, refunds, credited = await _read(session, razorpay, run.window_from, now)
            run.status = "ok"
        except ProblemError:
            run.status, run.error = "failed", "razorpay_unavailable"
    await session.flush()
    if run.status == "ok":
        check = _Run(session, run, settings)
        await check.compare(payments, refunds, credited, now)
        if run.opened:
            await publish(
                session,
                events.RECONCILIATION_MISMATCH,
                {"run_id": str(run.id), "opened": run.opened, "kinds": sorted(check.kinds)},
            )
    run.finished_at = datetime.now(UTC)
    await session.flush()
    summary = {"checked": run.checked, "healed": run.healed, "opened": run.opened}
    return {"status": run.status, **summary}


# --- the admin's side ---------------------------------------------------------------------------


def item_not_found() -> ProblemError:
    return ProblemError(404, "billing.recon_not_found", "No such reconciliation item.")


async def list_items(
    session: AsyncSession, status: str | None, kind: str | None, cursor: str | None, limit: int
) -> tuple[list[ReconciliationItem], str | None]:
    """Newest first (uuidv7 ids)."""
    stmt = select(ReconciliationItem)
    if status is not None:
        stmt = stmt.where(ReconciliationItem.status == status)
    if kind is not None:
        stmt = stmt.where(ReconciliationItem.kind == kind)
    if cursor is not None:
        try:
            before = uuid.UUID(str(decode_cursor(cursor).get("before")))
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(ReconciliationItem.id < before)
    found = list(
        (
            await session.execute(stmt.order_by(ReconciliationItem.id.desc()).limit(limit + 1))
        ).scalars()
    )
    more = len(found) > limit
    found = found[:limit]
    return found, encode_cursor({"before": str(found[-1].id)}) if more else None


async def last_run(session: AsyncSession) -> ReconciliationRun | None:
    found = await session.execute(
        select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(1)
    )
    return found.scalar_one_or_none()


async def resolve(
    session: AsyncSession, item_id: uuid.UUID, admin_id: uuid.UUID, note: str
) -> ReconciliationItem:
    """Close an open item with the admin's decision (audit-logged). It moves no money."""
    item = await session.get(ReconciliationItem, item_id, with_for_update=True)
    if item is None:
        raise item_not_found()
    if item.status != "open":
        raise ProblemError(
            409, "billing.recon_not_open", "This item is not open.", extra={"status": item.status}
        )
    item.status, item.resolution_note = "resolved", note
    item.resolved_by, item.resolved_at = admin_id, datetime.now(UTC)
    await audit.record(
        session,
        actor_user_id=admin_id,
        action="reconciliation.resolved",
        entity_type="reconciliation_item",
        entity_id=str(item.id),
        before={"status": "open"},
        after={"status": "resolved", "note": note},
    )
    await session.flush()
    await session.refresh(item)
    return item
