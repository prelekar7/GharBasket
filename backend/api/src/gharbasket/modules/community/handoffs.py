"""Hand-offs to NGOs (M9 §3.5): items from verified donations, food from claimed surplus offers,
and meals that customers donated with their Kindness points, each with photo proof.

A hand-off the NGO's coordinator records is confirmed at once; one the hub records waits for the
coordinator's confirmation. Donated meals are pledges (from `billing.points_donated`) at the
donor's household hub; a `meals` hand-off serves the hub's oldest unserved pledges first, splitting
one when needed. Nothing here moves money in the prototype, and no answer to an NGO coordinator
names a donor's household, flat or name.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import ColumnElement, func, or_, select, true, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import donations, events
from gharbasket.modules.community.drives import IST, half_up
from gharbasket.modules.community.models import (
    Donation,
    HandoffMeal,
    MealPledge,
    Ngo,
    NgoHandoff,
    NgoOffer,
    Society,
)
from gharbasket.modules.community.ngo_schemas import ConfirmIn, HandoffIn
from gharbasket.modules.community.ngos import Scope, does_not_accept, ngo_not_found, serves
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import files
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor
from gharbasket.platform.permissions import forbidden

log = structlog.get_logger("gharbasket.community.handoffs")

PHOTO_PURPOSE = "handoff_photo"
PHOTO_RETENTION = timedelta(days=3 * 365 + 1)  # 3 years, as the audit log (§11)
MAX_PHOTOS = 6  # 1 to 3 when recorded, up to 3 more at the confirmation
CLOCK_SKEW = timedelta(minutes=5)


def handoff_not_found() -> ProblemError:
    return ProblemError(404, "community.handoff_not_found", "No such hand-off.")


def photo_key_invalid() -> ProblemError:
    return ProblemError(
        422,
        "community.photo_key_invalid",
        "Attach 1 to 3 photos you uploaded for this hand-off (goods, never faces).",
    )


# --- donated meals (billing.points_donated) -------------------------------------------------------


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def on_points_donated(session: AsyncSession, event: OutboxEvent) -> None:
    """A meal pledge at the donor's household hub, once per M5 journal entry (a redelivered event
    counts once). Never raises on a bad payload."""
    entry_id, user_id = _uuid(event.payload.get("entry_id")), _uuid(event.payload.get("user_id"))
    meals = event.payload.get("meals")
    if entry_id is None or user_id is None or type(meals) is not int or meals <= 0:
        log.warning("points_donated_skipped", event_id=str(event.id), reason="payload")
        return
    household = await identity_api.household_for_user(session, user_id)
    hub_id = None if household is None else household.hub_id
    if household is not None and hub_id is None:
        society = await session.get(Society, household.society_id)
        hub_id = None if society is None else society.hub_id
    await session.execute(
        insert(MealPledge)
        .values(
            entry_id=entry_id,
            user_id=user_id,
            hub_id=hub_id,
            meals=meals,
            pledged_at=event.created_at,
            data_source="real" if household is None else household.data_source,
        )
        .on_conflict_do_nothing(index_elements=[MealPledge.entry_id])
    )


async def meals_waiting(session: AsyncSession, hub_id: uuid.UUID) -> int:
    waiting = await session.scalar(
        select(func.coalesce(func.sum(MealPledge.meals - MealPledge.served), 0)).where(
            MealPledge.hub_id == hub_id, MealPledge.served < MealPledge.meals
        )
    )
    return int(waiting or 0)


# --- who may see a hub's hand-offs ----------------------------------------------------------------


async def hub_reached(session: AsyncSession, scope: Scope, hub_id: uuid.UUID) -> None:
    """403 unless the caller manages the hub, is an admin, or coordinates an NGO serving it."""
    if scope.manages(hub_id):
        return
    if scope.ngo_ids:
        found = await session.execute(
            select(Ngo.id).where(Ngo.id.in_(scope.ngo_ids), Ngo.hub_ids.contains([hub_id]))
        )
        if found.first() is not None:
            return
    raise forbidden("Not a hub you or your NGO serve.")


async def ready(
    session: AsyncSession, scope: Scope, hub_id: uuid.UUID
) -> tuple[list[Donation], list[NgoOffer], int]:
    """What the hub can hand over: verified donations, claimed offers (a coordinator's own NGOs'
    only, unless she also manages the hub) and the meals waiting."""
    await hub_reached(session, scope, hub_id)
    ready_donations = await session.execute(
        select(Donation)
        .where(
            Donation.hub_id == hub_id,
            Donation.status == "verified",
            Donation.handoff_id.is_(None),
        )
        .order_by(Donation.verified_at, Donation.id)
    )
    offers = select(NgoOffer).where(
        NgoOffer.hub_id == hub_id, NgoOffer.status == "claimed", NgoOffer.handoff_id.is_(None)
    )
    if not scope.manages(hub_id):
        offers = offers.where(NgoOffer.claimed_by_ngo_id.in_(scope.ngo_ids))
    claimed = await session.execute(offers.order_by(NgoOffer.claimed_at, NgoOffer.offer_id))
    return (
        list(ready_donations.scalars()),
        list(claimed.scalars()),
        await meals_waiting(session, hub_id),
    )


def ready_lines(donation: Donation) -> list[dict[str, Any]]:
    return [
        {"code": line["code"], "unit": line["unit"], "qty": line["counted"]}
        for line in donation.items
        if line.get("counted")
    ]


# --- listing --------------------------------------------------------------------------------------


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    try:
        first = date.fromisoformat(f"{month}-01")
    except ValueError as exc:
        raise ProblemError(422, "request.invalid", "month is YYYY-MM.") from exc
    after = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return datetime.combine(first, time(), IST), datetime.combine(after, time(), IST)


async def _seen(
    session: AsyncSession, scope: Scope, hub_id: uuid.UUID | None, ngo_id: uuid.UUID | None
) -> ColumnElement[bool]:
    """Admins: every hand-off; hub managers: their hubs'; coordinators: their own NGOs'."""
    if hub_id is not None:
        await hub_reached(session, scope, hub_id)
    if ngo_id is not None and not scope.admin and ngo_id not in scope.ngo_ids:
        if not scope.hub_ids:
            raise ngo_not_found()
        managed = await session.execute(
            select(Ngo.id).where(Ngo.id == ngo_id, Ngo.hub_ids.overlap(list(scope.hub_ids)))
        )
        if managed.first() is None:
            raise ngo_not_found()
    if scope.admin:
        return true()
    terms: list[ColumnElement[bool]] = []
    if scope.hub_ids:
        terms.append(NgoHandoff.hub_id.in_(scope.hub_ids))
    if scope.ngo_ids:
        terms.append(NgoHandoff.ngo_id.in_(scope.ngo_ids))
    return or_(*terms) if terms else NgoHandoff.id.is_(None)


async def page(
    session: AsyncSession,
    scope: Scope,
    *,
    hub_id: uuid.UUID | None,
    ngo_id: uuid.UUID | None,
    month: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[NgoHandoff], str | None, dict[str, Any]]:
    """Newest first, keyset-paginated on (handed_at, id), with the report over the whole filter."""
    where = [await _seen(session, scope, hub_id, ngo_id)]
    if hub_id is not None:
        where.append(NgoHandoff.hub_id == hub_id)
    if ngo_id is not None:
        where.append(NgoHandoff.ngo_id == ngo_id)
    if month is not None:
        start, end = _month_bounds(month)
        where += [NgoHandoff.handed_at >= start, NgoHandoff.handed_at < end]
    totals = (
        await session.execute(
            select(
                func.count(NgoHandoff.id),
                func.coalesce(func.sum(NgoHandoff.items_count), 0),
                func.coalesce(func.sum(NgoHandoff.kg_tenths), 0),
                func.coalesce(func.sum(NgoHandoff.meals), 0),
                func.coalesce(func.sum(NgoHandoff.people_reached), 0),
            ).where(*where)
        )
    ).one()
    count, items, kg_tenths, meals, reached = (int(value or 0) for value in totals)
    report = {
        "handoffs": count,
        "items": items,
        "kg": kg_tenths / 10,
        "meals": meals,
        "people_reached": reached,
    }
    stmt = select(NgoHandoff).where(*where)
    if cursor is not None:
        position = decode_cursor(cursor)
        try:
            after = (datetime.fromisoformat(position["at"]), uuid.UUID(position["id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(tuple_(NgoHandoff.handed_at, NgoHandoff.id) < after)
    rows = await session.execute(
        stmt.order_by(NgoHandoff.handed_at.desc(), NgoHandoff.id.desc()).limit(limit + 1)
    )
    found = list(rows.scalars())
    if len(found) <= limit:
        return found, None, report
    last = found[limit - 1]
    next_cursor = encode_cursor({"at": last.handed_at.isoformat(), "id": str(last.id)})
    return found[:limit], next_cursor, report


async def links(
    session: AsyncSession, handoff_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, list[uuid.UUID]], dict[uuid.UUID, list[uuid.UUID]]]:
    """The donation ids and offer ids of each hand-off."""
    gave: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in handoff_ids}
    took: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in handoff_ids}
    if handoff_ids:
        for donation_id, handoff_id in await session.execute(
            select(Donation.id, Donation.handoff_id)
            .where(Donation.handoff_id.in_(handoff_ids))
            .order_by(Donation.id)
        ):
            if handoff_id is not None:
                gave[handoff_id].append(donation_id)
        for offer_id, handoff_id in await session.execute(
            select(NgoOffer.offer_id, NgoOffer.handoff_id)
            .where(NgoOffer.handoff_id.in_(handoff_ids))
            .order_by(NgoOffer.offer_id)
        ):
            if handoff_id is not None:
                took[handoff_id].append(offer_id)
    return gave, took


# --- recording ------------------------------------------------------------------------------------


async def claim_photos(
    session: AsyncSession, keys: list[str], user_id: uuid.UUID, store: files.ObjectStore | None
) -> list[str]:
    """The caller's own `handoff_photo` uploads, checked in storage and claimed for M9; kept 3
    years from now."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return []
    if store is None:
        raise files.storage_unavailable()
    used = await session.execute(
        select(NgoHandoff.id).where(NgoHandoff.photo_keys.overlap(keys)).limit(1)
    )
    if used.first() is not None:
        raise photo_key_invalid()
    for key in keys:
        upload = await files.verify(session, key, user_id=user_id, purpose=PHOTO_PURPOSE)
        stored = await store.head(key) if upload is not None else None
        if (
            upload is None
            or stored is None
            or (stored.content_type, stored.size_bytes) != (upload.content_type, upload.size_bytes)
            or await files.claim(
                session, key, user_id=user_id, purpose=PHOTO_PURPOSE, by="community"
            )
            is None
        ):
            raise photo_key_invalid()
        await files.set_delete_after(session, key, donations.now() + PHOTO_RETENTION)
    return keys


def _kind_matches(body: HandoffIn) -> bool:
    given = {
        "items": bool(body.donation_ids),
        "food": bool(body.offer_ids),
        "meals": body.meals > 0,
    }
    return given[body.kind] and not any(on for kind, on in given.items() if kind != body.kind)


def counted(donation: Donation) -> tuple[int, int]:
    """(items, kg tenths) the hub counted: pieces, plus kilograms counted as one item per kg."""
    pieces = sum(Decimal(str(line.get("counted") or 0)) for line in donation.items)
    kg = sum(
        Decimal(str(line.get("counted") or 0)) for line in donation.items if line["unit"] == "kg"
    )
    return half_up(Decimal(pieces)), half_up(Decimal(kg) * 10)


async def _hand_over_items(
    session: AsyncSession, handoff: NgoHandoff, donation_ids: list[uuid.UUID], by: uuid.UUID
) -> None:
    rows = await session.execute(
        select(Donation)
        .where(Donation.id.in_(donation_ids))
        .order_by(Donation.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    found = {d.id: d for d in rows.scalars()}
    not_ready = [
        str(i)
        for i in donation_ids
        if (d := found.get(i)) is None
        or d.hub_id != handoff.hub_id
        or d.status != "verified"
        or d.handoff_id is not None
    ]
    if not_ready:
        raise ProblemError(
            409,
            "community.donation_not_ready",
            "Only verified donations of this hub, not handed over yet, can be given.",
            extra={"donation_ids": not_ready},
        )
    items = kg = 0
    for donation_id in sorted(donation_ids):
        donation = found[donation_id]
        donation.handoff_id = handoff.id
        donation.version += 1
        await donations.record_event(session, donation, "handed_over", by)
        pieces, tenths = counted(donation)
        items, kg = items + pieces, kg + tenths
    handoff.items_count, handoff.kg_tenths = items, kg


async def _hand_over_food(
    session: AsyncSession, handoff: NgoHandoff, offer_ids: list[uuid.UUID]
) -> None:
    rows = await session.execute(
        select(NgoOffer)
        .where(NgoOffer.offer_id.in_(offer_ids))
        .order_by(NgoOffer.offer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    found = {o.offer_id: o for o in rows.scalars()}
    not_ready = [
        str(i)
        for i in offer_ids
        if (o := found.get(i)) is None
        or o.hub_id != handoff.hub_id
        or o.status != "claimed"
        or o.claimed_by_ngo_id != handoff.ngo_id
        or o.handoff_id is not None
    ]
    if not_ready:
        raise ProblemError(
            409,
            "community.offer_not_ready",
            "Only offers this NGO claimed at this hub, not handed over yet, can be given.",
            extra={"offer_ids": not_ready},
        )
    for offer in found.values():
        offer.handoff_id = handoff.id
    handoff.kg_tenths = sum(o.kg_tenths or 0 for o in found.values())


async def _serve_meals(session: AsyncSession, handoff: NgoHandoff, meals: int) -> None:
    """The hub's oldest unserved pledges first, splitting the last one when needed. The pledges
    are locked, so two hand-offs at once never serve one meal twice."""
    rows = await session.execute(
        select(MealPledge)
        .where(MealPledge.hub_id == handoff.hub_id, MealPledge.served < MealPledge.meals)
        .order_by(MealPledge.pledged_at, MealPledge.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    pledges = list(rows.scalars())
    waiting = sum(p.meals - p.served for p in pledges)
    if meals > waiting:
        raise ProblemError(
            422,
            "community.meals_exceed_pledged",
            "More meals than are waiting at this hub.",
            extra={"meals_waiting": waiting},
        )
    left = meals
    for pledge in pledges:
        if left == 0:
            break
        take = min(left, pledge.meals - pledge.served)
        pledge.served += take
        session.add(HandoffMeal(handoff_id=handoff.id, pledge_id=pledge.id, meals=take))
        left -= take
    handoff.meals = meals
    await session.flush()


async def record(
    session: AsyncSession,
    scope: Scope,
    body: HandoffIn,
    by: uuid.UUID,
    store: files.ObjectStore | None,
) -> NgoHandoff:
    """A coordinator of the NGO records it confirmed; a hub manager of the hub records it to be
    confirmed. Publishes `community.handoff_recorded`."""
    as_ngo = body.ngo_id in scope.ngo_ids
    if not as_ngo and body.hub_id not in scope.hub_ids:
        raise forbidden("Record hand-offs of your own NGO, or at a hub you manage.")
    ngo = await session.get(Ngo, body.ngo_id)
    if ngo is None or (not as_ngo and body.hub_id not in ngo.hub_ids):
        raise ngo_not_found()
    if not serves(ngo, body.hub_id, body.kind):
        raise does_not_accept(
            f"This NGO does not take {body.kind} at this hub (paused, not verified, or another "
            "hub)."
        )
    if not _kind_matches(body):
        raise ProblemError(
            422,
            "community.handoff_empty",
            "Name the donations (items), the offers (food) or the meals (meals), and only those.",
        )
    if not body.photo_keys:
        raise photo_key_invalid()
    at = donations.now()
    handed_at = body.handed_at or at
    if handed_at.tzinfo is None or handed_at > at + CLOCK_SKEW:
        raise ProblemError(422, "request.invalid", "handed_at is a past time with its zone.")
    photos = await claim_photos(session, body.photo_keys, by, store)
    handoff = NgoHandoff(
        ngo_id=ngo.id,
        hub_id=body.hub_id,
        kind=body.kind,
        photo_keys=photos,
        people_reached=body.people_reached,
        handed_at=handed_at,
        recorded_by="ngo" if as_ngo else "hub",
        recorded_by_user_id=by,
        confirmed_at=at if as_ngo else None,
        confirmed_by_user_id=by if as_ngo else None,
        note=body.note,
        data_source=ngo.data_source,
    )
    session.add(handoff)
    await session.flush()
    if body.kind == "items":
        await _hand_over_items(session, handoff, body.donation_ids, by)
    elif body.kind == "food":
        await _hand_over_food(session, handoff, body.offer_ids)
    else:
        await _serve_meals(session, handoff, body.meals)
    await session.flush()
    await session.refresh(handoff)
    await publish(
        session,
        events.HANDOFF_RECORDED,
        {
            "handoff_id": str(handoff.id),
            "ngo_id": str(handoff.ngo_id),
            "hub_id": str(handoff.hub_id),
            "kind": handoff.kind,
            "items": handoff.items_count,
            "kg_tenths": handoff.kg_tenths,
            "meals": handoff.meals,
        },
    )
    return handoff


async def confirm(
    session: AsyncSession,
    scope: Scope,
    handoff_id: uuid.UUID,
    body: ConfirmIn,
    by: uuid.UUID,
    store: files.ObjectStore | None,
) -> NgoHandoff:
    """The NGO's coordinator confirms receipt of a hand-off the hub recorded, once."""
    handoff = await session.get(
        NgoHandoff, handoff_id, with_for_update=True, populate_existing=True
    )
    if handoff is None or handoff.ngo_id not in scope.ngo_ids:
        raise handoff_not_found()
    if handoff.confirmed_at is not None:
        raise ProblemError(
            409, "community.handoff_already_confirmed", "This hand-off is already confirmed."
        )
    keys = [k for k in dict.fromkeys(body.photo_keys) if k not in handoff.photo_keys]
    if len(handoff.photo_keys) + len(keys) > MAX_PHOTOS:
        raise photo_key_invalid()
    added = await claim_photos(session, keys, by, store)
    handoff.photo_keys = [*handoff.photo_keys, *added]
    if body.people_reached is not None:
        handoff.people_reached = body.people_reached
    handoff.confirmed_at, handoff.confirmed_by_user_id = donations.now(), by
    await session.flush()
    return handoff


# --- the donation's NGO, for the household's list -------------------------------------------------


async def ngo_names_for(session: AsyncSession, handoff_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Hand-off id -> the NGO's name ("Given to Annapurna Food Bank")."""
    if not handoff_ids:
        return {}
    rows = await session.execute(
        select(NgoHandoff.id, Ngo.name)
        .join(Ngo, Ngo.id == NgoHandoff.ngo_id)
        .where(NgoHandoff.id.in_(handoff_ids))
    )
    return {handoff_id: name for handoff_id, name in rows.all()}
