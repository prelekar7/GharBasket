"""NGO partners and the surplus food offered to them (M9 §3.5).

An admin lists NGOs: the hubs each collects from and what it accepts (items, food, meals). Its
coordinators are M1 role grants, `ngo_coordinator:<ngo id>`. When M6's 12:30 cascade offers surplus
to an NGO (`hub_ops.surplus_offered`, offered_to ngo), M9 copies the offer if an active, verified
NGO serves the hub and accepts food; the first coordinator to claim it wins, and the same
transaction settles M6's offer as taken by that NGO.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import ColumnElement, and_, false, or_, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community.drives import half_up
from gharbasket.modules.community.models import Ngo, NgoOffer
from gharbasket.modules.community.ngo_schemas import NgoIn, NgoPatch
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.permissions import Permit, forbidden

log = structlog.get_logger("gharbasket.community.ngos")

COORDINATOR = "ngo_coordinator"
HUB_STAFF = "hub_manager"


def now() -> datetime:
    return datetime.now(UTC)


def ngo_not_found() -> ProblemError:
    return ProblemError(404, "community.ngo_not_found", "No such NGO.")


def offer_not_found() -> ProblemError:
    return ProblemError(404, "community.offer_not_found", "No such offer.")


def offer_expired() -> ProblemError:
    return ProblemError(
        409, "community.offer_expired", "This offer has expired or was settled at the hub."
    )


def does_not_accept(detail: str) -> ProblemError:
    return ProblemError(422, "community.ngo_does_not_accept", detail)


@dataclass(frozen=True)
class Scope:
    """What a caller reaches through §3.5's permissions: every hub and NGO (admin), the hubs she
    manages, and the NGOs she coordinates."""

    admin: bool
    hub_ids: frozenset[uuid.UUID]
    ngo_ids: frozenset[uuid.UUID]

    @classmethod
    def of(cls, permit: Permit) -> "Scope":
        def scoped(role: str) -> frozenset[uuid.UUID]:
            return frozenset(
                g.scope_id for g in permit.grants if g.role == role and g.scope_id is not None
            )

        return cls(permit.is_global, scoped(HUB_STAFF), scoped(COORDINATOR))

    def manages(self, hub_id: uuid.UUID) -> bool:
        return self.admin or hub_id in self.hub_ids


def serves(ngo: Ngo, hub_id: uuid.UUID, gift: str) -> bool:
    """The NGO may take this gift at this hub: active, verified, serving it, accepting it."""
    return ngo.status == "active" and ngo.verified and hub_id in ngo.hub_ids and gift in ngo.accepts


def eligible(gift: str, hub_id: uuid.UUID | None = None) -> ColumnElement[bool]:
    """SQL for `serves` (any hub when `hub_id` is None)."""
    where = and_(Ngo.status == "active", Ngo.verified, Ngo.accepts.contains([gift]))
    return where if hub_id is None else and_(where, Ngo.hub_ids.contains([hub_id]))


async def coordinated(session: AsyncSession, scope: Scope, *, gift: str | None = None) -> list[Ngo]:
    """The caller's own NGOs (as coordinator), or only those that may take `gift` somewhere."""
    if not scope.ngo_ids:
        return []
    stmt = select(Ngo).where(Ngo.id.in_(scope.ngo_ids))
    if gift is not None:
        stmt = stmt.where(eligible(gift))
    return list((await session.execute(stmt.order_by(Ngo.id))).scalars())


async def served_hubs(
    session: AsyncSession, scope: Scope, gift: str | None = None
) -> set[uuid.UUID]:
    """Hubs the caller's NGOs collect from (that may take `gift` there, when given)."""
    return {hub for ngo in await coordinated(session, scope, gift=gift) for hub in ngo.hub_ids}


# --- the admin's NGO list -------------------------------------------------------------------------


async def list_ngos(session: AsyncSession, scope: Scope, hub_id: uuid.UUID | None) -> list[Ngo]:
    """Admins: every NGO; hub managers: those serving their hubs; coordinators: their own."""
    if hub_id is not None and not scope.manages(hub_id) and not scope.ngo_ids:
        raise forbidden("This hub is not yours.")
    seen: ColumnElement[bool] = true()
    if not scope.admin:
        mine = Ngo.id.in_(scope.ngo_ids) if scope.ngo_ids else false()
        managed = Ngo.hub_ids.overlap(list(scope.hub_ids)) if scope.hub_ids else false()
        seen = or_(mine, managed)
    stmt = select(Ngo).where(seen)
    if hub_id is not None:
        stmt = stmt.where(Ngo.hub_ids.contains([hub_id]))
    return list((await session.execute(stmt.order_by(Ngo.name, Ngo.id))).scalars())


async def _check_hubs(session: AsyncSession, hub_ids: list[uuid.UUID]) -> None:
    for hub_id in hub_ids:
        if await hub_ops_api.get_hub(session, hub_id) is None:
            raise ProblemError(
                422, "request.invalid", "No such hub.", extra={"hub_id": str(hub_id)}
            )


def _snapshot(ngo: Ngo) -> dict[str, Any]:
    return {
        "name": ngo.name,
        "kind": ngo.kind,
        "hub_ids": [str(h) for h in ngo.hub_ids],
        "accepts": list(ngo.accepts),
        "registration_ref": ngo.registration_ref,
        "verified": ngo.verified,
        "status": ngo.status,
        "note": ngo.note,
    }


async def create_ngo(session: AsyncSession, body: NgoIn, by: uuid.UUID) -> Ngo:
    await _check_hubs(session, body.hub_ids)
    ngo = Ngo(**body.model_dump())
    session.add(ngo)
    await session.flush()
    await session.refresh(ngo)
    await audit.record(
        session,
        actor_user_id=by,
        action="community.ngo_created",
        entity_type="ngo",
        entity_id=str(ngo.id),
        after=_snapshot(ngo),
    )
    return ngo


async def locked_ngo(session: AsyncSession, ngo_id: uuid.UUID) -> Ngo:
    ngo = await session.get(Ngo, ngo_id, with_for_update=True, populate_existing=True)
    if ngo is None:
        raise ngo_not_found()
    return ngo


async def patch_ngo(session: AsyncSession, ngo: Ngo, body: NgoPatch, by: uuid.UUID) -> None:
    sent = {name: getattr(body, name) for name in body.model_fields_set}
    if "hub_ids" in sent:
        await _check_hubs(session, sent["hub_ids"])
    before = _snapshot(ngo)
    changed = [name for name, value in sent.items() if getattr(ngo, name) != value]
    if not changed:
        return
    for name in changed:
        setattr(ngo, name, sent[name])
    ngo.version += 1
    await session.flush()
    after = _snapshot(ngo)
    await audit.record(
        session,
        actor_user_id=by,
        action="community.ngo_updated",
        entity_type="ngo",
        entity_id=str(ngo.id),
        before={name: before[name] for name in changed},
        after={name: after[name] for name in changed},
    )


async def names(session: AsyncSession, ngo_ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ngo_ids:
        return {}
    rows = await session.execute(select(Ngo.id, Ngo.name).where(Ngo.id.in_(ngo_ids)))
    return {ngo_id: name for ngo_id, name in rows.all()}


# --- surplus offers -------------------------------------------------------------------------------


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def weigh_tenths(qty: Decimal, unit: str, unit_g: float | None) -> int | None:
    """Tenths of a kg, by the rule of M6's waste logs: kg and l as themselves, a piece or bunch
    through the catalog's grams per unit, a pack (or a piece without them) unknown."""
    if unit in ("kg", "l"):
        kg = qty
    elif unit in ("piece", "bunch") and unit_g is not None:
        kg = qty * Decimal(str(unit_g)) / 1000
    else:
        return None
    return half_up(kg * 10)


async def on_surplus_offered(session: AsyncSession, event: OutboxEvent) -> None:
    """Copy an NGO offer for M9's coordinators, once, when an active, verified NGO serves the hub
    and accepts food; otherwise the hub manager settles it by phone (M6). Never raises on a bad
    payload."""
    payload = event.payload
    if payload.get("offered_to") != "ngo":
        return
    offer_id, hub_id = _uuid(payload.get("offer_id")), _uuid(payload.get("hub_id"))
    product_id = _uuid(payload.get("product_id"))
    try:
        qty = Decimal(str(payload.get("qty")))
        expires_at = datetime.fromisoformat(str(payload.get("expires_at")))
    except (InvalidOperation, ValueError):
        qty, expires_at = Decimal(0), None
    unit = payload.get("unit")
    if (
        offer_id is None
        or hub_id is None
        or product_id is None
        or not isinstance(unit, str)
        or expires_at is None
        or expires_at.tzinfo is None
        or not qty.is_finite()
        or qty <= 0
    ):
        log.warning("surplus_offer_skipped", event_id=str(event.id), reason="payload")
        return
    found = await session.execute(select(Ngo.id).where(eligible("food", hub_id)).limit(1))
    if found.first() is None:
        return
    product = (await catalog_api.stock_products(session, [product_id])).get(product_id)
    await session.execute(
        insert(NgoOffer)
        .values(
            offer_id=offer_id,
            hub_id=hub_id,
            product_id=product_id,
            qty=qty,
            unit=unit[:8],
            kg_tenths=weigh_tenths(qty, unit, None if product is None else product.unit_g),
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(index_elements=[NgoOffer.offer_id])
    )


def offer_status(offer: NgoOffer, at: datetime) -> str:
    if offer.status == "open" and offer.expires_at <= at:
        return "expired"
    return offer.status


def status_is(status: str, at: datetime) -> ColumnElement[bool]:
    if status == "open":
        return and_(NgoOffer.status == "open", NgoOffer.expires_at > at)
    if status == "expired":
        return or_(
            NgoOffer.status == "expired",
            and_(NgoOffer.status == "open", NgoOffer.expires_at <= at),
        )
    return NgoOffer.status == "claimed"


OFFER_LIMIT = 200


async def list_offers(
    session: AsyncSession, scope: Scope, hub_id: uuid.UUID | None, status: str
) -> list[NgoOffer]:
    """Soonest expiry first. Staff see their hubs' offers; a coordinator those of hubs where one of
    her NGOs may take food."""
    hubs: set[uuid.UUID] | None = None if scope.admin else set(scope.hub_ids)
    if hubs is not None:
        hubs |= await served_hubs(session, scope, "food")
    if hub_id is not None:
        if hubs is not None and hub_id not in hubs:
            raise forbidden("Not a hub you or your NGO serve.")
        hubs = {hub_id}
    stmt = select(NgoOffer).where(status_is(status, now()))
    if hubs is not None:
        stmt = stmt.where(NgoOffer.hub_id.in_(hubs))
    rows = await session.execute(
        stmt.order_by(NgoOffer.expires_at, NgoOffer.offer_id).limit(OFFER_LIMIT)
    )
    return list(rows.scalars())


async def claim(
    session: AsyncSession, scope: Scope, offer_id: uuid.UUID, ngo_id: uuid.UUID, by: uuid.UUID
) -> NgoOffer:
    """The first claim wins: the offer row is locked, so a second claim waits and then finds it
    claimed (409 `community.offer_taken`). M6's offer is settled as taken in the same
    transaction."""
    if ngo_id not in scope.ngo_ids:
        raise ngo_not_found()
    ngo = await session.get(Ngo, ngo_id)
    if ngo is None:
        raise ngo_not_found()
    offer = await session.get(NgoOffer, offer_id, with_for_update=True, populate_existing=True)
    if offer is None or offer.hub_id not in await served_hubs(session, scope):
        raise offer_not_found()
    if not serves(ngo, offer.hub_id, "food"):
        raise does_not_accept("This NGO cannot take food at this hub now.")
    at = now()
    if offer.status == "claimed":
        raise ProblemError(409, "community.offer_taken", "Another NGO claimed this offer first.")
    if offer_status(offer, at) == "expired":
        raise offer_expired()
    try:
        await hub_ops_api.settle_surplus(
            session,
            offer.offer_id,
            "taken",
            taken_by=hub_ops_api.Taker("ngo", name=ngo.name),
            actor_user_id=by,
            note=f"Claimed by {ngo.name} in the NGO console",
        )
    except ProblemError as error:
        if error.code not in ("hub_ops.offer_settled", "hub_ops.offer_not_found"):
            raise
        # Settled at the hub (by phone) before this claim; nothing is written here, and the copy
        # reads open until expires_at (no `hub_ops.surplus_settled` yet).
        raise offer_expired() from error
    offer.status, offer.claimed_by_ngo_id = "claimed", ngo.id
    offer.claimed_by_user_id, offer.claimed_at = by, at
    await session.flush()
    return offer
