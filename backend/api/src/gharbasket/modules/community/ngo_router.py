"""HTTP endpoints for NGOs, surplus offers to NGOs and hand-offs (M9 §3.5).

Admins list NGOs; hub managers see their hubs' NGOs, offers and hand-offs; an NGO's coordinators
(`ngo_coordinator:<ngo id>`) see their own NGO, claim offers at the hubs it serves, and record or
confirm its hand-offs. No answer names a donor's household, flat or name, and photo links are
5-minute signed GETs, minted only for these callers and audit-logged.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Response, status

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import handoffs, ngos
from gharbasket.modules.community.drives import IST
from gharbasket.modules.community.kindness_schemas import PhotoUrl
from gharbasket.modules.community.models import Ngo, NgoHandoff, NgoOffer
from gharbasket.modules.community.ngo_schemas import (
    ClaimIn,
    ConfirmIn,
    Coordinator,
    HandoffIn,
    HandoffOut,
    HandoffReport,
    HandoffsOut,
    HandoffSummary,
    NgoCard,
    NgoIn,
    NgoOfferOut,
    NgoOffersOut,
    NgoOut,
    NgoPatch,
    NgosOut,
    OfferProduct,
    OfferStatus,
    ReadyDonation,
    ReadyLine,
    ReadyOut,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit, files
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Limit
from gharbasket.platform.permissions import Permit, require

router = APIRouter()

CanReadNgos = Annotated[Permit, require("ngos:read")]
CanWriteNgos = Annotated[Permit, require("ngos:write")]
CanReadOffers = Annotated[Permit, require("ngo_offers:read")]
CanClaim = Annotated[Permit, require("ngo_offers:claim")]
CanReadHandoffs = Annotated[Permit, require("ngo_handoffs:read")]
CanWriteHandoffs = Annotated[Permit, require("ngo_handoffs:write")]
CanConfirm = Annotated[Permit, require("ngo_handoffs:confirm")]
Month = Annotated[
    str | None,
    Query(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="An IST month", examples=["2027-02"]),
]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _store(request: Request) -> files.ObjectStore | None:
    return getattr(request.app.state, "storage", None)


# --- NGOs -----------------------------------------------------------------------------------------


async def _ngos_out(session: DbSession, found: list[Ngo]) -> list[NgoOut]:
    contacts = await identity_api.first_role_holders(
        session, ngos.COORDINATOR, [ngo.id for ngo in found]
    )
    out: list[NgoOut] = []
    for ngo in found:
        contact = contacts.get(ngo.id)
        out.append(
            NgoOut(
                id=ngo.id,
                name=ngo.name,
                kind=ngo.kind,  # pyright: ignore[reportArgumentType]
                hub_ids=list(ngo.hub_ids),
                accepts=list(ngo.accepts),  # pyright: ignore[reportArgumentType]
                registration_ref=ngo.registration_ref,
                verified=ngo.verified,
                status=ngo.status,  # pyright: ignore[reportArgumentType]
                note=ngo.note,
                coordinator=None
                if contact is None
                else Coordinator(name=contact.name, phone_e164=contact.phone_e164),
                version=ngo.version,
            )
        )
    return out


@router.get(
    "/ngos",
    summary="NGO partners: every one (admin), your hubs' (hub manager), your own (coordinator)",
    responses={403: _problem("auth.forbidden: another hub")},
)
async def list_ngos(
    permit: CanReadNgos, session: DbSession, hub_id: uuid.UUID | None = None
) -> NgosOut:
    found = await ngos.list_ngos(session, ngos.Scope.of(permit), hub_id)
    return NgosOut(items=await _ngos_out(session, found))


@router.post(
    "/admin/ngos",
    status_code=status.HTTP_201_CREATED,
    summary="List an NGO partner (admin; Idempotency-Key; coordinators are M1 role grants)",
    responses={
        400: _problem("request.idempotency_key_required"),
        403: _problem("auth.forbidden"),
        422: _problem("request.invalid (an unknown hub), request.idempotency_mismatch"),
    },
)
async def create_ngo(
    body: NgoIn, permit: CanWriteNgos, session: DbSession, idem: Idempotent, response: Response
) -> NgoOut:
    ngo = await ngos.create_ngo(session, body, permit.principal.user_id)
    out = (await _ngos_out(session, [ngo]))[0]
    await idem.save(201, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.patch(
    "/admin/ngos/{ngo_id}",
    summary="Change an NGO partner (admin; If-Match: its version; audit-logged)",
    responses={
        403: _problem("auth.forbidden"),
        404: _problem("community.ngo_not_found"),
        412: _problem("request.version_mismatch"),
        422: _problem("request.invalid (an unknown hub)"),
        428: _problem("request.precondition_required"),
    },
)
async def patch_ngo(
    ngo_id: uuid.UUID,
    body: NgoPatch,
    permit: CanWriteNgos,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> NgoOut:
    ngo = await ngos.locked_ngo(session, ngo_id)
    if ngo.version != expected:
        raise ProblemError(
            412, "request.version_mismatch", "This changed since you loaded it; reload and retry."
        )
    await ngos.patch_ngo(session, ngo, body, permit.principal.user_id)
    out = (await _ngos_out(session, [ngo]))[0]
    await session.commit()
    set_etag(response, out.version)
    return out


# --- surplus offers -------------------------------------------------------------------------------


async def offers_out(
    session: DbSession, found: list[NgoOffer], lang: Language
) -> list[NgoOfferOut]:
    products = await catalog_api.stock_products(session, list({o.product_id for o in found}))
    claimers = await ngos.names(
        session, {o.claimed_by_ngo_id for o in found if o.claimed_by_ngo_id is not None}
    )
    at = ngos.now()
    out: list[NgoOfferOut] = []
    for offer in found:
        product = products.get(offer.product_id)
        claimer = offer.claimed_by_ngo_id
        out.append(
            NgoOfferOut(
                offer_id=offer.offer_id,
                hub_id=offer.hub_id,
                product=OfferProduct(
                    id=offer.product_id,
                    name="" if product is None else localised(product.names, lang),
                ),
                qty=float(offer.qty),
                unit=offer.unit,
                kg=None if offer.kg_tenths is None else offer.kg_tenths / 10,
                expires_at=offer.expires_at,
                status=ngos.offer_status(offer, at),  # pyright: ignore[reportArgumentType]
                claimed_by=None
                if claimer is None
                else NgoCard(id=claimer, name=claimers.get(claimer, "")),
            )
        )
    return out


@router.get(
    "/ngo/offers",
    summary="Surplus food offered to NGOs, soonest expiry first (at most 200)",
    responses={403: _problem("auth.forbidden: a hub you and your NGO do not serve")},
)
async def list_offers(
    permit: CanReadOffers,
    session: DbSession,
    lang: Lang,
    hub_id: uuid.UUID | None = None,
    status: Annotated[OfferStatus, Query(description="Default: open")] = "open",
) -> NgoOffersOut:
    found = await ngos.list_offers(session, ngos.Scope.of(permit), hub_id, status)
    return NgoOffersOut(items=await offers_out(session, found, lang))


@router.post(
    "/ngo/offers/{offer_id}/claim",
    summary="Claim a surplus offer for your NGO: the first claim wins (Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        404: _problem("community.offer_not_found, community.ngo_not_found (not your NGO)"),
        409: _problem("community.offer_taken, community.offer_expired"),
        422: _problem("community.ngo_does_not_accept, request.idempotency_mismatch"),
    },
)
async def claim_offer(
    offer_id: uuid.UUID,
    body: ClaimIn,
    permit: CanClaim,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> NgoOfferOut:
    offer = await ngos.claim(
        session, ngos.Scope.of(permit), offer_id, body.ngo_id, permit.principal.user_id
    )
    out = (await offers_out(session, [offer], lang))[0]
    await idem.save(200, out)
    await session.commit()
    return out


# --- hand-offs ------------------------------------------------------------------------------------


async def handoffs_out(
    session: DbSession, permit: Permit, found: list[NgoHandoff], request: Request
) -> list[HandoffOut]:
    """Every caller of these endpoints is the hub's staff, an admin or the NGO's coordinator, so
    each gets the photo links; minting them is audit-logged."""
    gave, took = await handoffs.links(session, [h.id for h in found])
    names = await ngos.names(session, {h.ngo_id for h in found})
    store = _store(request)
    out: list[HandoffOut] = []
    for handoff in found:
        photos: list[PhotoUrl] = []
        if store is not None:  # none while storage is down; `photos` still counts
            expires = ngos.now() + files.GET_TTL
            photos = [
                PhotoUrl(url=store.signed_get(k), expires_at=expires) for k in handoff.photo_keys
            ]
            await audit.record(
                session,
                actor_user_id=permit.principal.user_id,
                action="community.handoff_photo_links",
                entity_type="ngo_handoff",
                entity_id=str(handoff.id),
                after={"photos": len(photos)},
            )
        out.append(
            HandoffOut(
                id=handoff.id,
                ngo=NgoCard(id=handoff.ngo_id, name=names.get(handoff.ngo_id, "")),
                hub_id=handoff.hub_id,
                kind=handoff.kind,  # pyright: ignore[reportArgumentType]
                donation_ids=gave[handoff.id],
                offer_ids=took[handoff.id],
                meals=handoff.meals,
                summary=HandoffSummary(
                    items=handoff.items_count, kg=handoff.kg_tenths / 10, meals=handoff.meals
                ),
                photos=len(handoff.photo_keys),
                photo_urls=photos,
                people_reached=handoff.people_reached,
                handed_at=handoff.handed_at,
                recorded_by=handoff.recorded_by,  # pyright: ignore[reportArgumentType]
                confirmed_at=handoff.confirmed_at,
                note=handoff.note,
            )
        )
    return out


@router.get(
    "/ngo-handoffs/ready",
    summary="What the hub can hand over: verified donations, claimed offers, meals waiting",
    responses={403: _problem("auth.forbidden: a hub you and your NGO do not serve")},
)
async def ready(
    hub_id: uuid.UUID, permit: CanReadHandoffs, session: DbSession, lang: Lang
) -> ReadyOut:
    found, offers, waiting = await handoffs.ready(session, ngos.Scope.of(permit), hub_id)
    return ReadyOut(
        donations=[
            ReadyDonation(
                id=d.id,
                items=[ReadyLine.model_validate(line) for line in handoffs.ready_lines(d)],
                verified_on=(d.verified_at or d.updated_at).astimezone(IST).date(),
            )
            for d in found
        ],
        offers=await offers_out(session, offers, lang),
        meals_waiting=waiting,
    )


@router.get(
    "/ngo-handoffs",
    summary="Hand-offs, newest first, with the report over the whole filter",
    responses={
        403: _problem("auth.forbidden: a hub you and your NGO do not serve"),
        404: _problem("community.ngo_not_found (not an NGO you see)"),
        422: _problem("request.invalid_cursor, request.invalid"),
    },
)
async def list_handoffs(
    permit: CanReadHandoffs,
    session: DbSession,
    request: Request,
    response: Response,
    hub_id: uuid.UUID | None = None,
    ngo_id: uuid.UUID | None = None,
    month: Month = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> HandoffsOut:
    found, next_cursor, report = await handoffs.page(
        session,
        ngos.Scope.of(permit),
        hub_id=hub_id,
        ngo_id=ngo_id,
        month=month,
        cursor=cursor,
        limit=limit,
    )
    items = await handoffs_out(session, permit, found, request)
    await session.commit()  # the audit of minted photo links
    response.headers["Cache-Control"] = "no-store"
    return HandoffsOut(items=items, next_cursor=next_cursor, report=HandoffReport(**report))


@router.post(
    "/ngo-handoffs",
    status_code=status.HTTP_201_CREATED,
    summary="Record a hand-off of items, food or meals with photos (Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        403: _problem("auth.forbidden: not your NGO, nor a hub you manage"),
        404: _problem("community.ngo_not_found"),
        409: _problem(
            "community.donation_not_ready (donation_ids), community.offer_not_ready (offer_ids)"
        ),
        422: _problem(
            "community.ngo_does_not_accept, community.handoff_empty, "
            "community.meals_exceed_pledged (meals_waiting), community.photo_key_invalid, "
            "request.invalid, request.idempotency_mismatch"
        ),
        503: _problem("platform.storage_unavailable"),
    },
)
async def record_handoff(
    body: HandoffIn,
    permit: CanWriteHandoffs,
    session: DbSession,
    idem: Idempotent,
    request: Request,
) -> HandoffOut:
    handoff = await handoffs.record(
        session, ngos.Scope.of(permit), body, permit.principal.user_id, _store(request)
    )
    out = (await handoffs_out(session, permit, [handoff], request))[0]
    await idem.save(201, out)
    await session.commit()
    return out


@router.post(
    "/ngo-handoffs/{handoff_id}/confirm",
    summary="Confirm receipt of a hand-off the hub recorded (your NGO's; Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        404: _problem("community.handoff_not_found"),
        409: _problem("community.handoff_already_confirmed"),
        422: _problem("community.photo_key_invalid, request.idempotency_mismatch"),
        503: _problem("platform.storage_unavailable"),
    },
)
async def confirm_handoff(
    handoff_id: uuid.UUID,
    body: ConfirmIn,
    permit: CanConfirm,
    session: DbSession,
    idem: Idempotent,
    request: Request,
) -> HandoffOut:
    handoff = await handoffs.confirm(
        session, ngos.Scope.of(permit), handoff_id, body, permit.principal.user_id, _store(request)
    )
    out = (await handoffs_out(session, permit, [handoff], request))[0]
    await idem.save(200, out)
    await session.commit()
    return out
