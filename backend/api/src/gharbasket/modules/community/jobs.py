"""Consumers and jobs of M9 Community & Kindness (spec §5, §6): captains follow M8's verification
and suspension (§3.2). `partners.reinstated` is deliberately not consumed: the hub manager decides
whether a reinstated captain serves again. `sweep_missed_pickups` closes each delivery day's
donation pickups (§3.4)."""

from typing import Any

from gharbasket.modules.community import donations, drives
from gharbasket.modules.community.captains import on_partner_suspended, on_partner_verified
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.outbox import subscribe

subscribe(partners_api.VERIFIED, on_partner_verified)
subscribe(partners_api.SUSPENDED, on_partner_suspended)


@job(
    "sweep_missed_pickups",
    "Daily 22:30 IST: today's donation pickups that no stop reported count as not ready",
)
async def sweep_missed_pickups(ctx: JobContext) -> dict[str, Any]:
    today = ctx.scheduled_for.astimezone(drives.IST).date()
    counts: dict[str, Any] = await donations.sweep_missed(ctx.session, today)
    counts["date"] = today.isoformat()
    return counts
