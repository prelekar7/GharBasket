"""Society unlock (M9 §3.6): `prospect → unlocking → live`, the threshold, and the pre-live fee.

- `prospect → unlocking` when the first captain is appointed (captains.appoint, same transaction)
  or when the hub says so.
- `unlocking → live` once the households that joined reach the threshold: checked when a household
  is created or moves in, and after any threshold change. Going live sets `live_at` and publishes
  `community.society_live`, both once in the society's life; nothing ever moves it back.
- The threshold `N = clamp(ceil(stop cost ÷ ₹40), 10, 60)` comes from M10's stop cost, weekly.
"""

import math
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import events
from gharbasket.modules.community.models import Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish

# What one household's box carries towards a stop (the console's delivery-per-box assumption).
DELIVERY_ALLOWANCE_PAISE = 4_000
THRESHOLD_MIN, THRESHOLD_MAX = 10, 60
# The delivery fee on each box until the society goes live. It becomes the hub's
# `pre_live_box_fee_paise` (M6 follow-up, §11); until then every hub has the proposed default.
PRE_LIVE_BOX_FEE_PAISE = 3_000


def box_fee_paise(society: Society) -> int:
    """`live_at` decides, not `status`: a live society paused and resumed never pays again."""
    return 0 if society.live_at is not None else PRE_LIVE_BOX_FEE_PAISE


def threshold_for(stop_cost_paise: int) -> int:
    n = math.ceil(stop_cost_paise / DELIVERY_ALLOWANCE_PAISE)
    return min(max(n, THRESHOLD_MIN), THRESHOLD_MAX)


async def stop_cost(session: AsyncSession, society_id: uuid.UUID) -> int | None:
    """The marginal cost in paise of adding the society as a stop, from M10
    (`intel_gateway.api.society_stop_cost`, an M10 follow-up on `/slots/price`). None while M10
    cannot price it, which today is always: the job then keeps the current threshold."""
    return None


async def _locked(session: AsyncSession, society_id: uuid.UUID) -> Society | None:
    return (
        await session.execute(select(Society).where(Society.id == society_id).with_for_update())
    ).scalar_one_or_none()


async def go_live(session: AsyncSession, society: Society) -> None:
    """Make the society live; the first time ever, set `live_at` and publish `society_live`."""
    society.status = "live"
    if society.live_at is not None:
        await session.flush()
        return
    society.live_at = live_at = datetime.now(UTC)
    await session.flush()
    await publish(
        session,
        events.SOCIETY_LIVE,
        {
            "society_id": str(society.id),
            "hub_id": None if society.hub_id is None else str(society.hub_id),
            "joined": await identity_api.households_in_society(session, society.id),
            "threshold": society.unlock_threshold,
            "live_at": live_at.isoformat(),
        },
    )


async def check_live(session: AsyncSession, society: Society) -> bool:
    """An `unlocking` society whose households reached the threshold goes live. The caller holds
    the society's row lock, so two households arriving together publish once."""
    if society.status != "unlocking" or society.live_at is not None:
        return False
    joined = await identity_api.households_in_society(session, society.id)
    if joined < society.unlock_threshold:
        return False
    await go_live(session, society)
    return True


async def start_unlocking(session: AsyncSession, society: Society) -> None:
    """The first captain: a `prospect` society starts counting (captains.appoint)."""
    if society.status == "prospect":
        society.status = "unlocking"
        await session.flush()
        await check_live(session, society)


async def update(
    session: AsyncSession, society: Society, changes: dict[str, Any], by_user_id: uuid.UUID
) -> Society:
    """The hub's PATCH (§3.6): pin or set the threshold, or set the status. Audit-logged."""
    before = {
        "status": society.status,
        "unlock_threshold": society.unlock_threshold,
        "unlock_threshold_pinned": society.unlock_threshold_pinned,
    }
    if "unlock_threshold" in changes:
        society.unlock_threshold = changes["unlock_threshold"]
        society.threshold_source = "hub"
    if "unlock_threshold_pinned" in changes:
        society.unlock_threshold_pinned = changes["unlock_threshold_pinned"]
    status = changes.get("status")
    if status == "live":
        await go_live(session, society)
    elif status is not None:
        society.status = status
    await session.flush()
    await check_live(session, society)
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="community.society_updated",
        entity_type="society",
        entity_id=str(society.id),
        before=before,
        after={key: getattr(society, key) for key in before},
    )
    return society


async def recompute_thresholds(session: AsyncSession) -> dict[str, int]:
    """The weekly job: price each society that is not live and not pinned. Without a price the
    threshold (and its source) stays; a new threshold at or below `joined` makes it live."""
    counts = {"priced": 0, "kept": 0, "skipped": 0, "went_live": 0}
    ids = (await session.execute(select(Society.id).order_by(Society.id))).scalars().all()
    for society_id in ids:
        society = await _locked(session, society_id)
        if society is None or society.status == "live" or society.live_at is not None:
            counts["skipped"] += 1
            continue
        if society.unlock_threshold_pinned:
            counts["skipped"] += 1
            continue
        cost = await stop_cost(session, society.id)
        if cost is None:
            counts["kept"] += 1
            continue
        society.unlock_threshold = threshold_for(cost)
        society.threshold_source = "model"
        society.threshold_computed_at = datetime.now(UTC)
        await session.flush()
        counts["priced"] += 1
        counts["went_live"] += await check_live(session, society)
    return counts


# --- consumers of M1's household events (registered in jobs.py) --------------------------------


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _check_society(session: AsyncSession, society_id: uuid.UUID | None) -> None:
    society = None if society_id is None else await _locked(session, society_id)
    if society is not None:
        await check_live(session, society)


async def on_household_created(session: AsyncSession, event: OutboxEvent) -> None:
    """A new household may be the one that unlocks its society. Never raises on a bad payload."""
    await _check_society(session, _uuid(event.payload.get("society_id")))


async def on_household_updated(session: AsyncSession, event: OutboxEvent) -> None:
    """A household that moved society counts in the new one; the old one never goes back."""
    changed = event.payload.get("changed")
    household_id = _uuid(event.payload.get("household_id"))
    if not isinstance(changed, list) or "society_id" not in changed or household_id is None:
        return
    owner = await identity_api.owner_of_household(session, household_id)
    household = None if owner is None else await identity_api.household_for_user(session, owner)
    await _check_society(session, None if household is None else household.society_id)
