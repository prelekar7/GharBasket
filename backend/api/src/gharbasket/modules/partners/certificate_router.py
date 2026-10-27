"""HTTP endpoints of M8 for the income certificate (spec §3.7): a partner's own, and one issued by
hub staff. The answer is a 5-minute link to a PDF the server wrote (M0 never streams file bytes)."""

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Query, Request, Response
from redis.asyncio import Redis
from sqlalchemy import func, select

from gharbasket.modules.partners import certificate, service
from gharbasket.modules.partners.models import Partner
from gharbasket.modules.partners.schemas import CertificateLinkOut
from gharbasket.platform import audit, files, rate_limit
from gharbasket.platform.db import DbSession
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import Valkey

log = structlog.get_logger("gharbasket.partners.certificate")
router = APIRouter(tags=["partners"])

CanRead = Annotated[Permit, require("earnings:read")]
CanCertify = Annotated[Permit, require("earnings:certify")]
AppSettings = Annotated[Settings, Depends(get_settings)]
FromMonth = Annotated[str | None, Query(alias="from", max_length=7, examples=["2026-05"])]
ToMonth = Annotated[str | None, Query(max_length=7, examples=["2026-10"])]
KEEP = timedelta(days=1)  # `sweep_uploads` deletes the object after a day
LIMIT_USER, LIMIT_STAFF = 10, 60  # an hour's worth
RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "auth.invalid_token", "content": {"application/problem+json": {}}},
    403: {"description": "auth.forbidden", "content": {"application/problem+json": {}}},
    404: {
        "description": "partners.partner_not_found",
        "content": {"application/problem+json": {}},
    },
    422: {
        "description": "partners.certificate_period_invalid (with max_months), request.invalid",
        "content": {"application/problem+json": {}},
    },
    429: {
        "description": "partners.certificate_rate_limited (see Retry-After)",
        "content": {"application/problem+json": {}},
    },
    503: {
        "description": "platform.storage_unavailable",
        "content": {"application/problem+json": {}},
    },
}


def _storage(request: Request) -> files.ObjectStore:
    store: files.ObjectStore | None = getattr(request.app.state, "storage", None)
    if store is None:
        raise files.storage_unavailable()
    return store


async def issue(
    store: files.ObjectStore,
    session: DbSession,
    settings: Settings,
    *,
    records: list[Partner],
    first: str,
    last: str,
    actor_user_id: uuid.UUID,
    now: datetime,
) -> CertificateLinkOut:
    """Render, store, audit and commit one certificate for the records of one person; then mint
    the link. A failed put is 503 and nothing is recorded (§3.7)."""
    user_id = records[0].user_id
    facts = await certificate.collect(session, records, first, last, now)
    reference = certificate.new_reference()
    body = await asyncio.to_thread(
        certificate.render,
        facts,
        reference=reference,
        issued_at=now,
        issuer=settings.partners_certificate_issuer,
        simulated=settings.payouts_provider == "simulated",
    )
    key = certificate.new_key(user_id, (await session.execute(select(func.uuidv7()))).scalar_one())
    filename = f"gharbasket-income-certificate-{first}-to-{last}.pdf"
    await files.record(
        session,
        key,
        user_id=user_id,
        purpose="income_certificate",
        content_type="application/pdf",
        size_bytes=len(body),
        by="partners",
        delete_after=now + KEEP,
    )
    await store.put(
        key, body, "application/pdf", content_disposition=f'attachment; filename="{filename}"'
    )
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="partners.income_certificate_issued",
        entity_type="income_certificate",
        entity_id=reference,
        after={
            "user_id": str(user_id),
            "partner_ids": [str(p.id) for p in records],
            "from": first,
            "to": last,
            "earned_paise": facts.earned_paise,
            "paid_out_paise": facts.paid_out_paise,
            "owed_paise": facts.months.owed_paise,
            "sha256": hashlib.sha256(body).hexdigest(),
            "key": key,
        },
    )
    await session.commit()
    log.info("income_certificate_issued", reference=reference, months=len(facts.months.months))
    return CertificateLinkOut.model_validate(
        {
            "reference": reference,
            "from": first,
            "to": last,
            "url": store.signed_get(key),
            "filename": filename,
            "expires_at": now.replace(microsecond=0) + files.GET_TTL,
        }
    )


@router.get(
    "/earnings/certificate",
    summary="Your income certificate: a 5-minute link to a PDF of your earnings by month",
    responses=RESPONSES,
)
async def my_certificate(
    request: Request,
    permit: CanRead,
    session: DbSession,
    valkey: Valkey,
    settings: AppSettings,
    response: Response,
    from_: FromMonth = None,
    to: ToMonth = None,
) -> CertificateLinkOut:
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    first, last = certificate.parse_period(from_, to, now)
    store = _storage(request)
    me = permit.principal.user_id
    await _limit(valkey, f"partners.certificate:user:{me}", LIMIT_USER)
    records = await service.for_user(session, me)  # every kind and status she has
    if not records:
        raise service.not_found()
    return await issue(
        store,
        session,
        settings,
        records=records,
        first=first,
        last=last,
        actor_user_id=me,
        now=now,
    )


@router.get(
    "/partners/{partner_id}/earnings/certificate",
    summary="A partner's income certificate, issued by hub staff (audit-logged)",
    responses=RESPONSES,
)
async def partner_certificate(
    partner_id: uuid.UUID,
    request: Request,
    permit: CanCertify,
    session: DbSession,
    valkey: Valkey,
    settings: AppSettings,
    response: Response,
    from_: FromMonth = None,
    to: ToMonth = None,
) -> CertificateLinkOut:
    response.headers["Cache-Control"] = "no-store"
    now = datetime.now(UTC)
    first, last = certificate.parse_period(from_, to, now)
    store = _storage(request)
    staff = permit.principal.user_id
    await _limit(valkey, f"partners.certificate:staff:{staff}", LIMIT_STAFF)
    partner = await service.get(session, partner_id)
    if partner.status == "left":
        raise service.not_found()
    permit.ensure(partner.hub_id)
    # The person's records in the hubs the caller may act in (an admin: all), never a `left` one.
    records = [
        p
        for p in await service.for_user(session, partner.user_id)
        if p.status != "left" and permit.allows(p.hub_id)
    ]
    return await issue(
        store,
        session,
        settings,
        records=records,
        first=first,
        last=last,
        actor_user_id=staff,
        now=now,
    )


async def _limit(valkey: Redis | None, name: str, limit: int) -> None:
    await rate_limit.enforce(
        valkey,
        name,
        limit=limit,
        window_s=3600,
        code="partners.certificate_rate_limited",
        detail="Too many certificates this hour. Try again later.",
    )
