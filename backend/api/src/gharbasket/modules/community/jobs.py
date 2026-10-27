"""Consumers and scheduled jobs of M9 Community & Kindness (spec §5, §6).

- Captains follow M8's verification and suspension (§3.2). `partners.reinstated` is deliberately
  not consumed: the hub manager decides whether a reinstated captain serves again.
- A new household, or one that moved society, may unlock its society (§3.6).
- A delivered box converts its household's referral; a deletion request voids open ones (§3.3).
- `sweep_missed_pickups` closes each delivery day's donation pickups (§3.4), and M7's
  `logistics.pickup_completed` records each one a stop reported (M7 §3.6).
- M6's surplus offers to NGOs are copied for the NGO coordinators, and M5's donated meals become
  pledges that `meals` hand-offs serve (§3.5).
"""

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import donations, drives, handoffs, ngos, referrals, unlock
from gharbasket.modules.community.captains import on_partner_suspended, on_partner_verified
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import api as logistics_api
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
subscribe(hub_ops_api.SURPLUS_OFFERED, ngos.on_surplus_offered)
subscribe(billing_api.POINTS_DONATED, handoffs.on_points_donated)
subscribe(logistics_api.PICKUP_COMPLETED, donations.on_pickup_completed)


@job(
    "recompute_unlock_thresholds",
    "Price each society's unlock threshold from M10's stop cost (Monday 02:10 IST)",
)
async def recompute_unlock_thresholds(ctx: JobContext) -> dict[str, Any]:
    today = ctx.scheduled_for.astimezone(IST).date()
    return await unlock.recompute_thresholds(ctx.session, ctx.http, ctx.settings, today)


@job("expire_referrals", "Void referrals still unconverted 90 days after joining (03:10 IST)")
async def expire_referrals(ctx: JobContext) -> dict[str, Any]:
    on = ctx.scheduled_for.astimezone(IST).date()
    return {"voided": await referrals.expire(ctx.session, on), "cutoff": str(on - timedelta(90))}


@job(
    "sweep_missed_pickups",
    "Daily 22:30 IST: today's donation pickups that no stop reported count as not ready",
)
async def sweep_missed_pickups(ctx: JobContext) -> dict[str, Any]:
    today = ctx.scheduled_for.astimezone(drives.IST).date()
    counts: dict[str, Any] = await donations.sweep_missed(ctx.session, today)
    counts["date"] = today.isoformat()
    return counts
