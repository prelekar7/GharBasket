"""Ratings and the know-your-cook card (spec §3.5).

A household's owner rates a delivered tiffin once, from delivery until 48 hours after it. The
kitchen's rating is the mean of her last 50 ratings, recomputed in the same transaction; it is
shown from 5 ratings on and feeds the assignment's rating floor (§3.4). The card is what the app
shows a customer about the kitchen that cooked: never an address, phone, pin or KYC detail.
`prompt_tiffin_ratings` asks households to rate at 14:30.
"""

import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import assignments, events, kitchens
from gharbasket.modules.partners.models import (
    Assignment,
    AssignmentOrder,
    Kitchen,
    Partner,
    Rating,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

WINDOW = timedelta(hours=48)  # from the delivery, as for order issues
RECENT = 50  # a kitchen's rating is the mean of this many of her latest ratings


def not_found() -> ProblemError:
    return ProblemError(404, "partners.rating_not_found", "No rating for this order.")


async def rate(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
    score: int,
    comment: str | None,
) -> Rating:
    """The owner of the household rates the tiffin once. 404 `partners.rating_not_found` for an
    order that is not hers, not a tiffin, or that no kitchen cooked; 409
    `partners.rating_window_closed` before delivery or after the 48 hours; 409
    `partners.rating_exists` for a second rating."""
    ref = await orders_api.get_order_ref(session, order_id)
    partner_id = await assignments.kitchen_of(session, order_id)
    if ref is None or ref.kind != "tiffin" or ref.owner_user_id != user_id or partner_id is None:
        raise not_found()
    if (
        ref.status != "delivered"
        or ref.delivered_at is None
        or kitchens.now() > ref.delivered_at + WINDOW
    ):
        raise ProblemError(
            409,
            "partners.rating_window_closed",
            "A tiffin can be rated from its delivery until 48 hours after it.",
        )
    # One rating at a time per kitchen, so her mean never misses one.
    partner = await session.get(Partner, partner_id, with_for_update=True)
    if await session.get(Rating, order_id) is not None:
        raise ProblemError(409, "partners.rating_exists", "You have rated this tiffin already.")
    rating = Rating(
        order_id=order_id,
        partner_id=partner_id,
        household_id=ref.household_id,
        user_id=user_id,
        score=score,
        comment=comment,
    )
    session.add(rating)
    await session.flush()
    if partner is not None:
        await refresh(session, partner)
    await session.refresh(rating)
    return rating


async def refresh(session: AsyncSession, partner: Partner) -> None:
    """Her rating: the mean of her last 50 ratings (two decimals), and how many she has had."""
    latest = (
        select(Rating.score)
        .where(Rating.partner_id == partner.id)
        .order_by(Rating.created_at.desc(), Rating.order_id)
        .limit(RECENT)
        .subquery()
    )
    mean = await session.scalar(select(func.avg(latest.c.score)))
    total = await session.scalar(
        select(func.count()).select_from(Rating).where(Rating.partner_id == partner.id)
    )
    partner.rating_avg = (
        None if mean is None else Decimal(mean).quantize(Decimal("0.01"), ROUND_HALF_UP)
    )
    partner.rating_count = int(total or 0)
    # `version` is the ETag of her own edits (display name): a new rating changes the rating
    # aggregate only, so it must not make a pending rename fail with 412.
    await session.flush()


async def of_order(session: AsyncSession, user_id: uuid.UUID, order_id: uuid.UUID) -> Rating:
    """The rating of one of her own orders; 404 for anyone else's order, or none given yet."""
    ref = await orders_api.get_order_ref(session, order_id)
    rating = await session.get(Rating, order_id)
    if ref is None or ref.owner_user_id != user_id or rating is None:
        raise not_found()
    return rating


async def given_by(session: AsyncSession, user_id: uuid.UUID) -> list[Rating]:
    rows = await session.execute(
        select(Rating).where(Rating.user_id == user_id).order_by(Rating.created_at)
    )
    return list(rows.scalars())


async def erase_for_user(session: AsyncSession, user_id: uuid.UUID) -> int:
    """M1's purge (M1 §3.5): her ratings lose the rater and the free-text comment; the score
    stays, so the kitchen's mean does not move."""
    erased = await session.execute(
        update(Rating)
        .where(Rating.user_id == user_id)
        .values(user_id=None, comment=None)
        .returning(Rating.order_id)
    )
    return len(erased.all())


async def portions_served(session: AsyncSession, partner_id: uuid.UUID) -> int:
    """Portions of hers that were delivered, all time."""
    total = await session.scalar(
        select(func.coalesce(func.sum(AssignmentOrder.portions), 0))
        .join(Assignment, Assignment.id == AssignmentOrder.assignment_id)
        .where(Assignment.partner_id == partner_id, AssignmentOrder.status == "delivered")
    )
    return int(total or 0)


async def card_rows(session: AsyncSession, partner_id: uuid.UUID) -> tuple[Partner, Kitchen] | None:
    """An active or paused kitchen and her kitchen row; None for anyone else."""
    partner = await session.get(Partner, partner_id)
    found = await session.get(Kitchen, partner_id)
    if partner is None or found is None or partner.status not in ("active", "paused"):
        return None
    return partner, found


async def prompt(session: AsyncSession, day: date, meal: str = "lunch") -> int:
    """`partners.rating_requested` for the day's delivered tiffins of one meal that nobody asked
    about yet and that are not rated, once per order (a rerun of the job adds nothing)."""
    rated = exists().where(Rating.order_id == AssignmentOrder.order_id)
    rows = (
        await session.execute(
            select(AssignmentOrder.order_id, Assignment.partner_id)
            .join(Assignment, Assignment.id == AssignmentOrder.assignment_id)
            .where(
                Assignment.on_date == day,
                Assignment.meal == meal,
                AssignmentOrder.status == "delivered",
                AssignmentOrder.prompted_at.is_(None),
                ~rated,
            )
            .order_by(AssignmentOrder.order_id)
            .with_for_update(of=AssignmentOrder)
        )
    ).all()
    for order_id, partner_id in rows:
        ref = await orders_api.get_order_ref(session, order_id)
        if ref is None:
            continue
        await publish(
            session,
            events.RATING_REQUESTED,
            {
                "order_id": str(order_id),
                "household_id": str(ref.household_id),
                "partner_id": str(partner_id),
            },
        )
        await session.execute(
            update(AssignmentOrder)
            .where(AssignmentOrder.order_id == order_id, AssignmentOrder.status == "delivered")
            .values(prompted_at=func.now())
        )
    return len(rows)
