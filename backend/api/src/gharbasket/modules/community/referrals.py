"""Referrals (M9 §3.3): share codes, the neighbour's claim, and the reward in Kindness points.

A neighbour who claims a code and gets a first weekly box earns both sides 250 points (M5 §3.6),
never money. The referrer is rewarded for at most 10 conversions per IST month; later ones still
count, with 0 points. Nothing here ever names the neighbour.
"""

import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import events
from gharbasket.modules.community.models import Referral, ReferralCode, Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
# Crockford base32 without I, L, O and U: easy to read out and type.
ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
CODE_LENGTH = 6
CLAIM_WINDOW = timedelta(days=14)
MONTHLY_CAP = 10
LATEST = 50
SHARE_TEXT: dict[Language, str] = {
    "en": "Join me on GharBasket: our week's food, planned and delivered. My code is {code}.",
    "hi": "GharBasket पर मेरे साथ जुड़ें: पूरे हफ़्ते का खाना, योजना और डिलीवरी। मेरा कोड {code} है।",
    "mr": "GharBasket वर माझ्यासोबत या: आठवड्याचं जेवण, नियोजन आणि डिलिव्हरी. माझा कोड {code} आहे.",
}


def today() -> date:
    return datetime.now(IST).date()


async def code_for(session: AsyncSession, user_id: uuid.UUID) -> str:
    """The user's code, made on first use. A clash with another user's code draws again."""
    for _ in range(8):
        code = (
            await session.execute(select(ReferralCode.code).where(ReferralCode.user_id == user_id))
        ).scalar_one_or_none()
        if code is not None:
            return code
        drawn = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
        await session.execute(
            insert(ReferralCode).values(user_id=user_id, code=drawn).on_conflict_do_nothing()
        )
    raise RuntimeError("could not draw a free referral code")


async def link_for(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, code: str
) -> str | None:
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        return None
    slug = (
        await session.execute(select(Society.slug).where(Society.id == household.society_id))
    ).scalar_one()
    return f"{settings.public_web_url.rstrip('/')}/s/{slug}?ref={code}"


def share_text(lang: Language, code: str, link: str | None) -> str:
    text = SHARE_TEXT[lang].format(code=code)
    return text if link is None else f"{text} {link}"


def _refused(status: int, code: str, detail: str) -> ProblemError:
    return ProblemError(status, f"community.{code}", detail)


async def claim(
    session: AsyncSession, settings: Settings, user_id: uuid.UUID, code: str
) -> Referral:
    """The neighbour claims a code; the points it promises are fixed now. Refusals, in order:
    422 `household_required`, 404 `referral_code_unknown`, 422 `referral_self`, 409
    `already_referred`, 409 `referral_window_closed`."""
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        raise _refused(422, "household_required", "Finish setting up your home first.")
    owner = (
        await session.execute(
            select(ReferralCode.user_id).where(ReferralCode.code == code.strip().upper())
        )
    ).scalar_one_or_none()
    if owner is None:
        raise _refused(404, "referral_code_unknown", "No one has this code.")
    referrer_home = await identity_api.household_for_user(session, owner)
    if owner == user_id or (referrer_home is not None and referrer_home.id == household.id):
        raise _refused(422, "referral_self", "This code is your own home's.")
    already = await session.execute(select(Referral.id).where(Referral.referee_user_id == user_id))
    if already.first() is not None:
        raise _refused(409, "already_referred", "You have already used a neighbour's code.")
    signed_up = await identity_api.signed_up_at(session, user_id)
    if (
        signed_up is None
        or datetime.now(UTC) - signed_up > CLAIM_WINDOW
        or await orders_api.has_delivered_box(session, household.id)
    ):
        raise _refused(
            409,
            "referral_window_closed",
            "A code can be used within 14 days of joining, before your first box arrives.",
        )
    referral = Referral(
        code=code.strip().upper(),
        referrer_user_id=owner,
        referee_user_id=user_id,
        referee_household_id=household.id,
        society_id=household.society_id,
        hub_id=household.hub_id,
        status="joined",
        joined_on=today(),
        referrer_points=settings.referral_points_referrer,
        referee_points=settings.referral_points_referee,
        data_source=household.data_source,
    )
    try:
        async with session.begin_nested():
            session.add(referral)
            await session.flush()
    except IntegrityError as exc:  # a second claim at the same moment
        raise _refused(
            409, "already_referred", "You have already used a neighbour's code."
        ) from exc
    return referral


# --- reading -----------------------------------------------------------------------------------


async def made_by(session: AsyncSession, user_id: uuid.UUID) -> list[Referral]:
    rows = await session.execute(
        select(Referral)
        .where(Referral.referrer_user_id == user_id)
        .order_by(Referral.id.desc())
        .limit(LATEST)
    )
    return list(rows.scalars())


async def summary(session: AsyncSession, user_id: uuid.UUID) -> dict[str, int]:
    """Neighbours who joined with the code (void ones aside), converted, and points earned."""
    joined, converted, points = (
        await session.execute(
            select(
                func.count().filter(Referral.status != "void"),
                func.count().filter(Referral.status == "converted"),
                func.coalesce(
                    func.sum(Referral.referrer_points).filter(Referral.status == "converted"), 0
                ),
            ).where(Referral.referrer_user_id == user_id)
        )
    ).one()
    return {"joined": joined, "converted": converted, "points_earned": int(points)}


async def referred(session: AsyncSession, user_id: uuid.UUID) -> Referral | None:
    return (
        await session.execute(select(Referral).where(Referral.referee_user_id == user_id))
    ).scalar_one_or_none()


# --- conversion --------------------------------------------------------------------------------


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


async def _rewarded_in_month(session: AsyncSession, referrer: uuid.UUID, day: date) -> int:
    start = day.replace(day=1)
    end = (start + timedelta(days=32)).replace(day=1)
    count = await session.execute(
        select(func.count()).where(
            Referral.referrer_user_id == referrer,
            Referral.status == "converted",
            Referral.converted_on >= start,
            Referral.converted_on < end,
            Referral.referrer_points > 0,
        )
    )
    return count.scalar_one()


async def on_order_delivered(session: AsyncSession, event: OutboxEvent) -> None:
    """The referee household's first delivered box converts its referral and pays both sides
    once (add-ons and tiffins convert nothing). Never raises on a bad payload; a redelivery
    finds the referral converted and does nothing."""
    payload = event.payload
    household_id, order_id = _uuid(payload.get("household_id")), _uuid(payload.get("order_id"))
    if payload.get("kind") != "box" or household_id is None or order_id is None:
        return
    referral = (
        await session.execute(
            select(Referral)
            .where(Referral.referee_household_id == household_id, Referral.status == "joined")
            .limit(1)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if referral is None:
        return
    day = (event.created_at or datetime.now(UTC)).astimezone(IST).date()
    # One referrer's conversions are counted one at a time, so the cap holds under concurrency.
    await session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"referral-cap:{referral.referrer_user_id}", 0)
            )
        )
    )
    capped = await _rewarded_in_month(session, referral.referrer_user_id, day) >= MONTHLY_CAP
    referee_points = referral.referee_points
    referrer_points = 0 if capped else referral.referrer_points
    if referee_points:
        entry = await billing_api.credit_points(
            session, referral.referee_user_id, referee_points, "referral_welcome", referral.id
        )
        referral.referee_entry_id = entry.id
    if referrer_points:
        entry = await billing_api.credit_points(
            session, referral.referrer_user_id, referrer_points, "referral", referral.id
        )
        referral.referrer_entry_id = entry.id
    referral.status = "converted"
    referral.converted_on = day
    referral.order_id = order_id
    referral.referrer_points = referrer_points
    await session.flush()
    await publish(
        session,
        events.REFERRAL_CONVERTED,
        {
            "referral_id": str(referral.id),
            "referrer_user_id": str(referral.referrer_user_id),
            "referee_user_id": str(referral.referee_user_id),
            "society_id": str(referral.society_id),
            "hub_id": None if referral.hub_id is None else str(referral.hub_id),
            "referrer_points": referrer_points,
            "referee_points": referee_points,
        },
    )
