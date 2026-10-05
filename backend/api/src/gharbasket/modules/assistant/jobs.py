"""Scheduled jobs of M13 (spec §6): `expire_proposals` every 10 minutes (cron-job.org)."""

from typing import Any

from gharbasket.modules.assistant import service
from gharbasket.platform.jobs import JobContext, job


@job("expire_proposals", "Expire the assistant's proposals nobody confirmed within 15 minutes")
async def expire_proposals(ctx: JobContext) -> dict[str, Any]:
    return {"expired": await service.expire_pending(ctx.session, ctx.scheduled_for)}
