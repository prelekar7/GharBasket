"""Jobs and consumers of M5 Billing & Ledger (spec §5, §6): Kindness points (§3.6).

- `expire_kindness_points` (daily 00:30 IST) takes back what is left of each lot past its expiry.
- The consumer of M9's `community.donation_verified` credits the donation's points, once per
  donation. It shares the relay's savepoint with the event's other consumers, so a payload it
  cannot use is logged and skipped, never raised.
"""

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import kindness
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

# M9's event (M9 §3.4, §5): { donation_id, user_id, household_id, hub_id, society_id, drive_id,
# points }. Named here rather than imported, so M5 does not depend on M9.
DONATION_VERIFIED = "community.donation_verified"
BATCH = 200
log = structlog.get_logger("gharbasket.billing.kindness")


@job("expire_kindness_points", "Take back the Kindness points of lots past their expiry")
async def expire_kindness_points(ctx: JobContext) -> dict[str, Any]:
    at = ctx.scheduled_for  # lots expire at the end of an IST day; the job runs at 00:30
    users = lots = points = 0
    last: uuid.UUID | None = None
    while batch := await kindness.users_with_expired_lots(ctx.session, at, after=last, limit=BATCH):
        for user_id in batch:
            expired, taken = await kindness.expire_lots(ctx.session, user_id, at)
            users, lots, points = users + 1, lots + expired, points + taken
            last = user_id
        await ctx.session.commit()
    return {"users": users, "lots": lots, "points": points}


def _donation(payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID, int] | None:
    try:
        donation_id = uuid.UUID(str(payload["donation_id"]))
        user_id = uuid.UUID(str(payload["user_id"]))
        points = payload["points"]
    except (KeyError, TypeError, ValueError):
        return None
    if type(points) is not int or points <= 0:
        return None
    return donation_id, user_id, points


async def on_donation_verified(session: AsyncSession, event: OutboxEvent) -> None:
    """Credit a verified donation's points (`credit_points(…, "donation", donation_id)`); a
    redelivery credits nothing more. A donation that earned 0 points credits nothing."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    found = _donation(payload)  # pyright: ignore[reportUnknownArgumentType]
    if found is None:
        if payload.get("points") != 0:  # pyright: ignore[reportUnknownMemberType]
            log.warning("donation_verified_unusable", event_id=str(event.id))
        return
    donation_id, user_id, points = found
    try:
        async with session.begin_nested():
            await kindness.credit_points(session, user_id, points, "donation", donation_id)
    except ProblemError as problem:  # the same donation again with other points
        log.warning(
            "donation_verified_conflict",
            event_id=str(event.id),
            donation_id=str(donation_id),
            code=problem.code,
        )


subscribe(DONATION_VERIFIED, on_donation_verified)
