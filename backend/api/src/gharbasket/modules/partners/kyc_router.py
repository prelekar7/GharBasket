"""HTTP endpoints of M8 for KYC documents and their file links (spec §3.1).

The applicant uploads documents for her own open application; hub staff of the application's hub
(and admins) read the files through 5-minute links, every call audit-logged.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from gharbasket.modules.partners import applications, kyc
from gharbasket.modules.partners.application_router import document_out
from gharbasket.modules.partners.models import Application, KycDocument
from gharbasket.modules.partners.schemas import (
    FileLinkOut,
    FileLinksOut,
    KycDocumentIn,
    KycDocumentOut,
)
from gharbasket.platform import audit, files
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.models import Upload
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["partners"])


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


CanReadFiles = Annotated[Permit, require("kyc_files:read")]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def _locked(session: DbSession, application_id: uuid.UUID) -> Application:
    application = await session.get(Application, application_id, with_for_update=True)
    if application is None:
        raise applications.not_found()
    return application


@router.post(
    "/kyc-documents",
    status_code=201,
    summary="Upload a KYC document for your open application (Idempotency-Key required)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        404: _problem("partners.application_not_found"),
        409: _problem("partners.application_closed"),
        422: _problem(
            "partners.doc_type_invalid, partners.number_invalid (with field), "
            "partners.aadhaar_not_accepted, partners.document_expired, "
            "partners.file_key_invalid, request.idempotency_mismatch, request.invalid"
        ),
        503: _problem("platform.storage_unavailable"),
    },
)
async def upload_document(
    body: KycDocumentIn,
    request: Request,
    principal: CurrentPrincipal,
    settings: AppSettings,
    session: DbSession,
    idem: Idempotent,
) -> KycDocumentOut:
    application = await _locked(session, body.application_id)
    if application.user_id != principal.user_id:
        raise applications.not_found()
    applications.ensure_open(application)
    document = await kyc.upload(
        session,
        application,
        body.model_dump(),
        settings=settings,
        store=getattr(request.app.state, "storage", None),
    )
    out = document_out(document)
    await idem.save(201, out)
    await session.commit()
    return out


@router.post(
    "/kyc-documents/{document_id}/file-links",
    summary="5-minute links to a document's files (hub staff of its hub, admin; audit-logged)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.document_not_found"),
        503: _problem("platform.storage_unavailable"),
    },
)
async def file_links(
    document_id: uuid.UUID,
    permit: CanReadFiles,
    session: DbSession,
    store: files.Storage,
) -> FileLinksOut:
    document = await session.get(KycDocument, document_id)
    application = (
        None if document is None else await session.get(Application, document.application_id)
    )
    if document is None or application is None or not permit.allows(application.hub_id):
        raise kyc.not_found()
    expires_at = datetime.now(UTC).replace(microsecond=0) + files.GET_TTL
    links = []
    for key in document.file_keys:
        upload = await session.get(Upload, key)
        links.append(
            FileLinkOut(
                url=store.signed_get(key),
                content_type=upload.content_type if upload else "application/octet-stream",
                expires_at=expires_at,
            )
        )
    await audit.record(
        session,
        actor_user_id=permit.principal.user_id,
        action="kyc.file_read",
        entity_type="kyc_document",
        entity_id=str(document.id),
        after={"application_id": str(application.id), "files": len(links)},
    )
    await session.commit()
    return FileLinksOut(files=links)
