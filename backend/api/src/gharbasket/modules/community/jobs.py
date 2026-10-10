"""Consumers and scheduled jobs of M9 Community & Kindness (spec §5, §6).

- Captains follow M8's verification and suspension (§3.2). `partners.reinstated` is deliberately
  not consumed: the hub manager decides whether a reinstated captain serves again.
- A new household, or one that moved society, may unlock its society (§3.6).
- A delivered box converts its household's referral; a deletion request voids open ones (§3.3).
"""

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from gharbasket.modules.community import referrals, unlock
from gharbasket.modules.community.captains import on_partner_suspended, on_partner_verified
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.outbox import subscribe

IST = ZoneInfo("Asia/Kolkata")

subscribe(partners_api.VERIFIED, on_partner_verified)
subscribe(partners_api.SUSPENDED, on_partner_suspended)
subscribe(identity_api.HOUSEHOLD_CREATED, unlock.on_household_created)
subscribe(identity_api.HOUSEHOLD_UPDATED, unlock.on_household_updated)
subscribe(identity_api.DELETION_REQUESTED, referrals.on_deletion_requested)
subscribe(orders_api.DELIVERED, referrals.on_order_delivered)


@job(
    "recompute_unlock_thresholds",
    "Price each society's unlock threshold from M10's stop cost (Monday 02:10 IST)",
)
async def recompute_unlock_thresholds(ctx: JobContext) -> dict[str, Any]:
    return await unlock.recompute_thresholds(ctx.session)


@job("expire_referrals", "Void referrals still unconverted 90 days after joining (03:10 IST)")
async def expire_referrals(ctx: JobContext) -> dict[str, Any]:
    on = ctx.scheduled_for.astimezone(IST).date()
    return {"voided": await referrals.expire(ctx.session, on), "cutoff": str(on - timedelta(90))}
