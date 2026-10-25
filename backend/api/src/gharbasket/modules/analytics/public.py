"""The public society page (spec §3.7): `/s/<slug>`'s data, for anyone, so aggregates only.

M9 gives the society (`public_society`: unlocking and live ones only) and its k-protected giving
(`society_impact`, null under 5 contributing households), M3 the delivery days. Nothing is read
from M12's views, so the page is current within the 5-minute cache. It carries no captain, no
flat or tower count of who joined, no household, no partner and no earnings, and the referral code
in the page's own URL never reaches this endpoint: the console adds it to `join_url`.
"""

from datetime import date
from urllib.parse import quote

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.impact_schemas import PublicImpactOut, PublicSocietyOut
from gharbasket.modules.analytics.kpis import BP, ratio
from gharbasket.modules.analytics.reads import IST
from gharbasket.modules.community import api as community_api
from gharbasket.modules.subscriptions import api as subs_api
from gharbasket.platform.settings import Settings

MIN_HOMES = 5  # homes joined are shown from here (k = 5, as M9 §3.7)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


async def page(session: AsyncSession, settings: Settings, slug: str) -> PublicSocietyOut | None:
    """The society's page, or None when it is unknown, a prospect or paused."""
    society = await community_api.public_society(session, slug)
    if society is None:
        return None
    giving = await community_api.society_impact(session, society.id, "all", with_hub=False)
    days = await subs_api.delivery_days(session, society.id) if society.status == "live" else []
    live_since: date | None = (
        None if society.live_at is None else society.live_at.astimezone(IST).date()
    )
    shown = giving is not None and giving.items_donated is not None
    return PublicSocietyOut.model_validate(
        {
            "slug": society.slug,
            "name": society.name,
            "area": society.area,
            "status": society.status,
            "homes_joined": society.joined if society.joined >= MIN_HOMES else None,
            "unlock_target": society.threshold,
            "progress_bp": ratio(min(society.joined, society.threshold), society.threshold, BP),
            "live_since": live_since,
            "delivery_days": [WEEKDAYS[day] for day in days],
            "impact": (
                PublicImpactOut(
                    items_donated=giving.items_donated or 0, meals_donated=giving.meals_donated or 0
                )
                if shown and giving is not None
                else None
            ),
            "join_url": f"{settings.app_web_url.rstrip('/')}/join?s={quote(society.slug)}",
        }
    )
