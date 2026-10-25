"""Partner payouts (spec §3.5): every Monday, what each partner is owed up to Sunday is paid out.

`weekly_payouts` (Monday 10:00 IST) pays the ISO week before its `scheduled_for` (Monday to
Sunday, IST), one savepoint per partner:
1. **What is owed** is the balance of `partner_payable:<partner>` at Sunday 23:59:59 IST: an
   earning counts by when the work was done (`earned_at`, as the earnings week does), any other
   entry by when it was posted, and every `payout` and `payout_reversed` entry counts whenever it
   was posted (each settles money owed before its own week ended).
2. **Earlier payouts first.** Her `held` and `failed` payouts are tried again before anything
   new: paid when she is now eligible, else held with the current reason. Their money is still
   in `partner_payable`, so a new row takes only what is owed beyond them, and nothing is ever
   paid twice.
3. **The minimum.** A new amount under ₹100 is carried to the next week with no row, except for a
   partner who `left`: her whole balance is paid.
4. **Eligibility** (`partners.api.payout_eligibility`): not eligible → a `held` row with the
   reason, nothing posted. Eligible → a `pending` row and its `payout` entry (partner_payable:P →
   payout_clearing, key `payout:<partner>:<week>`) in the same transaction. The row is unique per
   (partner, week), so a rerun finds it and pays nothing more.
5. **The provider** is called after that commit: `simulated` answers `processed` at once with
   `pout_sim_<id>`, never reading the bank account. A `failed` answer posts `payout_reversed`
   (payout_clearing → partner_payable:P) and leaves the money for the next run or a retry. Both
   publish `billing.payout_sent`. A row left `pending` (a crash before the provider answered) is
   sent by the next run.

A retry after a failure posts a new `payout` entry keyed by the attempt (`payout:<partner>:<week>:
<n>`, its reversal likewise), so every attempt moves money exactly once.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal, Protocol

import structlog
from sqlalchemy import DateTime, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, service
from gharbasket.modules.billing.earnings import payout_out, week_bounds
from gharbasket.modules.billing.models import (
    PAYOUT_OPEN,
    JournalEntry,
    LedgerAccount,
    Payout,
    Posting,
)
from gharbasket.modules.billing.schemas import AdminPayoutOut, PayoutPartner
from gharbasket.modules.billing.service import Leg
from gharbasket.modules.billing.wallet import IST
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor
from gharbasket.platform.settings import Settings

MIN_PAISE = 10_000  # ₹100: less is carried to the next week (except for a partner who left)
WEEK = re.compile(r"^(2\d{3})-W(\d{2})$")
SETTLEMENT_KINDS = ("payout", "payout_reversed")
log = structlog.get_logger("gharbasket.billing.payouts")


def payout_not_found() -> ProblemError:
    return ProblemError(404, "billing.payout_not_found", "No such payout.")


def week_invalid() -> ProblemError:
    return ProblemError(422, "billing.week_invalid", "The week is an ISO week as YYYY-Www.")


def parse_week(value: str) -> str:
    """`2027-W03` as given, or 422 `billing.week_invalid` (malformed or not a real ISO week)."""
    match = WEEK.match(value)
    if match is None:
        raise week_invalid()
    try:
        date.fromisocalendar(int(match[1]), int(match[2]), 1)
    except ValueError as exc:
        raise week_invalid() from exc
    return value


def week_before(at: datetime) -> str:
    """The ISO week before the IST week `at` falls in: Monday pays the week just ended."""
    today = at.astimezone(IST).date()
    iso = (today - timedelta(days=today.weekday() + 7)).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# --- the provider ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderAnswer:
    status: Literal["processed", "processing", "failed"]
    ref: str | None


class PayoutProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def send(self, session: AsyncSession, payout: Payout) -> ProviderAnswer: ...


class SimulatedProvider:
    """The prototype's provider (owner, 2026-10-09): no money moves, so it never reads the bank
    account and answers `processed` at once."""

    @property
    def name(self) -> str:
        return "simulated"

    async def send(self, session: AsyncSession, payout: Payout) -> ProviderAnswer:
        return ProviderAnswer("processed", f"pout_sim_{payout.id.hex}")


SIMULATED = SimulatedProvider()


def provider_for(settings: Settings) -> PayoutProvider:
    """PAYOUTS_PROVIDER. Settings already refuse `razorpay_route` at start (pilot only)."""
    if settings.payouts_provider == "simulated":
        return SIMULATED
    raise RuntimeError(f"PAYOUTS_PROVIDER={settings.payouts_provider} is not built (M5 §3.5)")


# --- the ledger --------------------------------------------------------------------------------


async def _payable(session: AsyncSession, partner_id: uuid.UUID) -> LedgerAccount:
    """Her `partner_payable` account, locked: one payout decision per partner at a time."""
    return await service.owned_account(
        session, "partner_payable", "partner", partner_id, kind="liability", lock=True
    )


async def owed_before(session: AsyncSession, payable: LedgerAccount, cutoff: datetime) -> int:
    """What she is owed for everything up to `cutoff` and not yet paid out (step 1 above)."""
    earned = cast(JournalEntry.details["earned_at"].astext, DateTime(timezone=True))
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Posting.amount), 0))
            .join(JournalEntry, JournalEntry.id == Posting.entry_id)
            .where(
                Posting.account_id == payable.id,
                or_(
                    JournalEntry.kind.in_(SETTLEMENT_KINDS),
                    func.coalesce(earned, JournalEntry.created_at) < cutoff,
                ),
            )
        )
    ).scalar_one()
    return -int(total)  # a liability: credits (earnings) are negative postings


def _key(prefix: str, partner_id: uuid.UUID, week: str, attempt: int) -> str:
    return f"{prefix}:{partner_id}:{week}" + ("" if attempt == 1 else f":{attempt}")


async def _post_payout(
    session: AsyncSession,
    payable: LedgerAccount,
    payout_id: uuid.UUID,
    partner_id: uuid.UUID,
    week: str,
    amount: int,
    attempt: int,
) -> JournalEntry:
    """The `payout` entry: the money leaves what she is owed for payout_clearing. Never more than
    she is owed, so `partner_payable` cannot go below zero."""
    if await service.balance(session, payable) < amount:
        raise ProblemError(
            409,
            "billing.payout_exceeds_payable",
            "The partner is owed less than this payout.",
            extra={"payout_id": str(payout_id)},
        )
    clearing = await service.platform_account(session, "payout_clearing")
    entry, _ = await service.post(
        session,
        kind="payout",
        key=_key("payout", partner_id, week, attempt),
        legs=[Leg(payable, amount), Leg(clearing, -amount)],
        ref_type="payout",
        ref_id=payout_id,
        details={"partner_id": str(partner_id), "week": week, "attempt": attempt},
    )
    return entry


async def _send_out(
    session: AsyncSession, payable: LedgerAccount, payout: Payout, provider: PayoutProvider
) -> None:
    """A held or failed payout becomes `pending` with a new `payout` entry of its own."""
    payout.attempts += 1
    entry = await _post_payout(
        session,
        payable,
        payout.id,
        payout.partner_id,
        payout.week,
        payout.amount_paise,
        payout.attempts,
    )
    payout.status, payout.hold_reason, payout.entry_id = "pending", None, entry.id
    payout.provider, payout.provider_ref = provider.name, None
    await session.flush()


# --- the weekly run ----------------------------------------------------------------------------


async def _pay_partner(
    session: AsyncSession,
    partner_id: uuid.UUID,
    week: str,
    cutoff: datetime,
    provider: PayoutProvider,
) -> tuple[str, int]:
    """One partner's part of the run: (what became of this week, earlier payouts sent again)."""
    payable = await _payable(session, partner_id)
    partner = await partners_api.get_partner(session, partner_id)
    if partner is None:
        raise LookupError(f"partner_payable of {partner_id} has no partner record")
    monday, sunday, _, _ = week_bounds(week)
    earlier = list(
        (
            await session.execute(
                select(Payout)
                .where(
                    Payout.partner_id == partner_id,
                    Payout.status.in_(PAYOUT_OPEN),
                    Payout.period_from <= monday,
                )
                .order_by(Payout.period_from)
                .with_for_update()
            )
        ).scalars()
    )
    eligibility: partners_api.PayoutEligibility | None = None
    retried = 0
    for payout in earlier:
        eligibility = eligibility or await partners_api.payout_eligibility(session, partner_id)
        if eligibility.eligible:
            await _send_out(session, payable, payout, provider)
            retried += 1
        elif (payout.status, payout.hold_reason) != ("held", eligibility.reason):
            payout.status, payout.hold_reason = "held", eligibility.reason
    await session.flush()
    this_week = await session.execute(
        select(Payout.id).where(Payout.partner_id == partner_id, Payout.week == week)
    )
    if this_week.scalar() is not None:
        return "existing", retried  # a rerun: the week is already decided
    waiting = sum(p.amount_paise for p in earlier if p.status in PAYOUT_OPEN)
    amount = await owed_before(session, payable, cutoff) - waiting
    if amount <= 0 or (amount < MIN_PAISE and partner.status != "left"):
        return "carried", retried
    eligibility = eligibility or await partners_api.payout_eligibility(session, partner_id)
    payout_id = (await session.execute(select(func.uuidv7()))).scalar_one()
    payout = Payout(
        id=payout_id,
        partner_id=partner_id,
        hub_id=partner.hub_id,
        week=week,
        period_from=monday,
        period_to=sunday,
        amount_paise=amount,
        status="held",
        hold_reason=eligibility.reason,
        provider=provider.name,
        attempts=0,
    )
    if eligibility.eligible:
        entry = await _post_payout(session, payable, payout_id, partner_id, week, amount, 1)
        payout.status, payout.entry_id, payout.attempts = "pending", entry.id, 1
    session.add(payout)
    await session.flush()
    return payout.status, retried


async def run_weekly(
    session: AsyncSession, settings: Settings, scheduled_for: datetime
) -> dict[str, Any]:
    """The `weekly_payouts` job: decide every partner's payout for the week before, commit, then
    send what is pending to the provider."""
    provider = provider_for(settings)
    week = week_before(scheduled_for)
    cutoff = week_bounds(week)[3]  # Monday 00:00 IST after the week
    partners = (
        await session.execute(
            select(LedgerAccount.owner_id)
            .where(
                LedgerAccount.code == "partner_payable",
                LedgerAccount.owner_type == "partner",
                LedgerAccount.currency == "INR",
            )
            .order_by(LedgerAccount.owner_id)
        )
    ).scalars()
    counts: dict[str, int] = dict.fromkeys(
        ("pending", "held", "carried", "existing", "retried", "failed"), 0
    )
    for partner_id in [p for p in partners.all() if p is not None]:
        try:
            async with session.begin_nested():  # one partner's failure stops only that one
                outcome, retried = await _pay_partner(session, partner_id, week, cutoff, provider)
        except Exception as exc:
            log.error("payout_failed", partner_id=str(partner_id), error=type(exc).__name__)
            counts["failed"] += 1
            continue
        counts[outcome] += 1
        counts["retried"] += retried
    await session.commit()  # rows and entries are on record before the provider hears of them
    return {"week": week, **counts, **await send_pending(session, provider)}


async def send_pending(
    session: AsyncSession, provider: PayoutProvider, *, only: uuid.UUID | None = None
) -> dict[str, int]:
    """Send `pending` payouts (or just `only`) to the provider, one transaction each."""
    counts = {"processed": 0, "processing": 0, "refused": 0, "send_failed": 0}
    seen: list[uuid.UUID] = []
    while True:
        stmt = select(Payout).where(Payout.status == "pending", Payout.id.not_in(seen))
        if only is not None:
            stmt = stmt.where(Payout.id == only)
        payout = (
            await session.execute(
                stmt.order_by(Payout.id).limit(1).with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if payout is None:
            break
        seen.append(payout.id)
        payout_id = payout.id
        try:
            async with session.begin_nested():
                answer = await provider.send(session, payout)
                await _settle(session, payout, answer)
        except Exception as exc:  # left pending: the next run sends it again
            log.error("payout_send_failed", payout_id=str(payout_id), error=type(exc).__name__)
            counts["send_failed"] += 1
            await session.commit()
            continue
        counts["refused" if answer.status == "failed" else answer.status] += 1
        await session.commit()
    return counts


async def _settle(session: AsyncSession, payout: Payout, answer: ProviderAnswer) -> None:
    payout.provider_ref = answer.ref
    if answer.status == "processing":  # a real provider's webhook settles it later
        payout.status = "processing"
        await session.flush()
        return
    if answer.status == "failed":
        payable = await service.owned_account(
            session, "partner_payable", "partner", payout.partner_id, kind="liability"
        )
        clearing = await service.platform_account(session, "payout_clearing")
        await service.post(
            session,
            kind="payout_reversed",
            key=_key("payout_reversed", payout.partner_id, payout.week, payout.attempts),
            legs=[Leg(clearing, payout.amount_paise), Leg(payable, -payout.amount_paise)],
            ref_type="payout",
            ref_id=payout.id,
            details={
                "partner_id": str(payout.partner_id),
                "week": payout.week,
                "attempt": payout.attempts,
            },
        )
        payout.status = "failed"
    else:
        payout.status, payout.processed_at = "processed", datetime.now(UTC)
    await session.flush()
    await publish(
        session,
        events.PAYOUT_SENT,
        {
            "payout_id": str(payout.id),
            "partner_id": str(payout.partner_id),
            "amount_paise": payout.amount_paise,
            "status": payout.status,
        },
    )


# --- the admin's retry -------------------------------------------------------------------------


async def retry(
    session: AsyncSession,
    payout_id: uuid.UUID,
    *,
    actor_user_id: uuid.UUID,
    provider: PayoutProvider,
) -> Payout:
    """POST /v1/admin/payouts/{id}/retry: a held or failed payout whose reason is fixed becomes
    `pending` with its `payout` entry (audit-logged); the caller commits, then sends it."""
    payout = await session.get(Payout, payout_id)
    if payout is None:
        raise payout_not_found()
    payable = await _payable(session, payout.partner_id)
    await session.refresh(payout, with_for_update=True)
    if payout.status not in PAYOUT_OPEN:
        raise ProblemError(
            409,
            "billing.payout_not_retryable",
            "Only a held or failed payout can be retried.",
            extra={"payout_status": payout.status},
        )
    eligibility = await partners_api.payout_eligibility(session, payout.partner_id)
    if not eligibility.eligible:
        raise ProblemError(
            409,
            "billing.payout_still_held",
            "The partner still cannot be paid; fix the reason first.",
            extra={"hold_reason": eligibility.reason},
        )
    before = {"status": payout.status, "hold_reason": payout.hold_reason}
    await _send_out(session, payable, payout, provider)
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="billing.payout_retried",
        entity_type="payout",
        entity_id=str(payout.id),
        before=before,
        after={
            "status": "pending",
            "amount_paise": payout.amount_paise,
            "attempt": payout.attempts,
        },
    )
    return payout


# --- reading -----------------------------------------------------------------------------------


def _before(cursor: str | None) -> uuid.UUID | None:
    if cursor is None:
        return None
    try:
        return uuid.UUID(str(decode_cursor(cursor).get("before")))
    except ValueError as exc:
        raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc


async def _page(stmt: Any, session: AsyncSession, cursor: str | None, limit: int) -> Any:
    before = _before(cursor)
    if before is not None:
        stmt = stmt.where(Payout.id < before)
    found = list(
        (await session.execute(stmt.order_by(Payout.id.desc()).limit(limit + 1))).scalars()
    )
    more = len(found) > limit
    found = found[:limit]
    return found, encode_cursor({"before": str(found[-1].id)}) if more else None


async def for_partners(
    session: AsyncSession, partner_ids: list[uuid.UUID], cursor: str | None, limit: int
) -> tuple[list[Payout], str | None]:
    """A partner's own payouts (all her records), newest first."""
    return await _page(
        select(Payout).where(Payout.partner_id.in_(partner_ids)), session, cursor, limit
    )


async def for_staff(
    session: AsyncSession,
    *,
    week: str | None,
    status: str | None,
    hub_ids: set[uuid.UUID] | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AdminPayoutOut], str | None]:
    """The admin list (every hub, or `hub_ids` for a hub manager), newest first, with each
    partner's name and kind from M8."""
    stmt = select(Payout)
    if week is not None:
        stmt = stmt.where(Payout.week == week)
    if status is not None:
        stmt = stmt.where(Payout.status == status)
    if hub_ids is not None:
        stmt = stmt.where(Payout.hub_id.in_(hub_ids))
    found, next_cursor = await _page(stmt, session, cursor, limit)
    refs = await partners_api.get_partners(session, [p.partner_id for p in found])
    out: list[AdminPayoutOut] = []
    for payout in found:
        ref = refs.get(payout.partner_id)
        partner = None
        if ref is not None:
            partner = PayoutPartner(display_name=ref.display_name, kind=ref.kind)  # pyright: ignore[reportArgumentType]
        out.append(AdminPayoutOut(**payout_out(payout).model_dump(), partner=partner))
    return out, next_cursor
