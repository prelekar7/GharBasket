"""Settling refunds to source (spec §3.3): what Razorpay says about each part.

- `refund.processed` settles a part; `refund.failed` fails it and puts its money back into the
  wallet (`refund_to_source_failed`, razorpay_clearing → wallet). It always fits under the cap,
  because the part held its room while in flight.
- On a deleting account a failed part is moved once to another top-up that can take it, otherwise
  to `unclaimed_balances`.
- Once every part is final the refund is `processed`, `failed` or `partly_failed`, and
  `billing.refund_to_source_settled` goes to M11.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, refunds, service
from gharbasket.modules.billing.models import IN_FLIGHT, Payment, Refund, RefundPart
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

FINAL = ("processed", "failed")  # a part's final states


async def mark_processing(session: AsyncSession, refund_id: uuid.UUID) -> None:
    refund = await session.get(Refund, refund_id, with_for_update=True)
    if refund is not None and refund.status == "pending":
        refund.status = "processing"


async def settle(session: AsyncSession, refund_id: uuid.UUID) -> None:
    """Once every part is final: processed (all the money went back), failed (none of it) or
    partly_failed, and `billing.refund_to_source_settled` for M11."""
    refund = await session.get(Refund, refund_id, with_for_update=True, populate_existing=True)
    if refund is None or refund.status in refunds.SETTLED:
        return
    parts = list(
        (
            await session.execute(select(RefundPart).where(RefundPart.refund_id == refund_id))
        ).scalars()
    )
    if any(part.status in IN_FLIGHT for part in parts):
        return
    processed = sum(p.amount_paise for p in parts if p.status == "processed")
    refund.status = (
        "processed"
        if processed == refund.amount_paise
        else "failed"
        if processed == 0
        else "partly_failed"
    )
    refund.settled_at = datetime.now(UTC)
    await publish(
        session,
        events.REFUND_TO_SOURCE_SETTLED,
        {
            "refund_id": str(refund.id),
            "user_id": str(refund.user_id),
            "amount_paise": refund.amount_paise,
            "processed_paise": processed,
            "status": refund.status,
        },
    )


async def handle_event(session: AsyncSession, event: dict[str, Any], settings: Settings) -> str:
    """Apply one `refund.*` event from Razorpay; returns what happened (stored with the webhook)."""
    kind = event.get("event")
    if kind not in ("refund.processed", "refund.failed"):
        return "ignored"
    try:
        entity = event["payload"]["refund"]["entity"]
        refund_ref, receipt = str(entity["id"]), entity.get("receipt")
    except (KeyError, TypeError):
        return "malformed"
    part = (
        await session.execute(
            select(RefundPart).where(RefundPart.provider_refund_id == refund_ref).with_for_update()
        )
    ).scalar_one_or_none()
    if part is None and receipt:  # the event beat the job's record of the answer
        try:
            part = await session.get(RefundPart, uuid.UUID(str(receipt)), with_for_update=True)
        except ValueError:
            part = None
        if part is not None and part.provider_refund_id not in (None, refund_ref):
            part = None
    if part is None:
        over_cap = await session.execute(
            select(Payment.id).where(Payment.provider_refund_id == refund_ref)
        )
        return "already_settled" if over_cap.first() else "unknown_refund"
    if part.status in FINAL:
        return "already_settled"
    part.provider_refund_id = refund_ref
    part.settled_at = datetime.now(UTC)
    if kind == "refund.processed":
        part.status = "processed"
    else:
        part.status = "failed"
        await _put_back(session, part, settings)
    await settle(session, part.refund_id)
    return "refund_processed" if part.status == "processed" else "refund_failed"


async def _put_back(session: AsyncSession, part: RefundPart, settings: Settings) -> None:
    """Razorpay failed a part: the money goes back into the wallet (it always fits, its room was
    held). On a deleting account it is moved once to another top-up, otherwise to unclaimed."""
    refund = await session.get(Refund, part.refund_id)
    assert refund is not None
    account = await refunds.wallet_account(session, refund.user_id)
    clearing = await service.platform_account(session, "razorpay_clearing")
    entry, _ = await service.post(
        session,
        kind="refund_to_source_failed",
        key=f"rts_failed:{part.id}",
        legs=[Leg(clearing, part.amount_paise), Leg(account, -part.amount_paise)],
        ref_type="refund",
        ref_id=refund.id,
        details={"part_id": str(part.id)},
    )
    part.failed_entry_id = entry.id
    await session.flush()
    deleting = (
        refund.reason == "account_deleted"
        or (
            await session.execute(
                select(Refund.id).where(
                    Refund.user_id == refund.user_id, Refund.reason == "account_deleted"
                )
            )
        ).first()
    )
    if not deleting:
        return
    if part.replaces_part_id is None:
        for room in await refunds.rooms(session, refund.user_id, settings, datetime.now(UTC)):
            if room.payment.id != part.payment_id and room.room_paise >= part.amount_paise:
                await refunds.add_part(
                    session, account, refund, room.payment, part.amount_paise, part.id
                )
                return
    await refunds.post_unclaimed(
        session, account, part.amount_paise, f"unclaimed:{part.id}", refund.id
    )
