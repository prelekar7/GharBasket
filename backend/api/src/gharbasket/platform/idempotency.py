"""`Idempotency-Key` for POSTs that create money movements or orders (TDD §4.1, M0 US-7).

The key is claimed inside the request's own transaction, and the response is stored in that same
transaction, so the business change and its stored answer commit together or not at all:

    @router.post("/wallet/topups", status_code=201)
    async def create_topup(body: TopupIn, session: DbSession, idem: Idempotent) -> TopupOut:
        topup = await service.create_topup(session, body)
        await idem.save(201, topup)
        await session.commit()
        return topup

- Same key + same request: the stored response is replayed (`Idempotent-Replayed: true`).
- Same key + different request: 422 `request.idempotency_mismatch`.
- Two copies at the same moment: the second waits for the first's transaction (Postgres blocks on
  the conflicting insert), then replays its answer. If the first fails, the second runs normally.
- Keys expire after 24 hours; an expired key is reused as if new.
"""

import hashlib
import json
from datetime import timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import IdempotencyKey

KEY_TTL = timedelta(hours=24)
REPLAY_HEADER = "Idempotent-Replayed"


class IdempotentReplay(Exception):
    def __init__(self, status_code: int, body: Any) -> None:
        super().__init__("idempotent replay")
        self.status_code = status_code
        self.body = body


class Idempotency:
    """A claimed key. Call `save()` with the response before committing."""

    def __init__(self, session: AsyncSession, scope: str, key: str) -> None:
        self._session = session
        self._scope = scope
        self.key = key

    async def save(self, status_code: int, body: Any) -> None:
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.scope == self._scope, IdempotencyKey.key == self.key)
            .values(status_code=status_code, response=jsonable_encoder(body))
        )


def _request_hash(method: str, path: str, body: bytes) -> str:
    try:
        canonical = json.dumps(json.loads(body), sort_keys=True, separators=(",", ":")).encode()
    except ValueError:
        canonical = body
    return hashlib.sha256(b"\n".join([method.encode(), path.encode(), canonical])).hexdigest()


def _valid_key(key: str) -> bool:
    return 1 <= len(key) <= 255 and key.isascii() and key.isprintable()


async def idempotency(
    request: Request,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Idempotency:
    if idempotency_key is None:
        raise ProblemError(
            400, "request.idempotency_key_required", "This request needs an Idempotency-Key header."
        )
    if not _valid_key(idempotency_key):
        raise ProblemError(
            400, "request.idempotency_key_invalid", "Use 1-255 printable ASCII characters."
        )

    # Until login exists every caller shares one scope; the auth PR sets the user id here.
    scope = str(getattr(request.state, "principal_id", None) or "anonymous")
    request_hash = _request_hash(request.method, request.url.path, await request.body())

    claim = (
        insert(IdempotencyKey)
        .values(scope=scope, key=idempotency_key, request_hash=request_hash)
        .on_conflict_do_update(
            index_elements=[IdempotencyKey.scope, IdempotencyKey.key],
            set_={
                "request_hash": request_hash,
                "status_code": None,
                "response": None,
                "created_at": func.now(),
            },
            # Only an expired key may be taken over.
            where=IdempotencyKey.created_at < func.now() - KEY_TTL,
        )
        .returning(IdempotencyKey.key)
    )
    if (await session.execute(claim)).first() is not None:
        return Idempotency(session, scope, idempotency_key)

    stored = await session.get(IdempotencyKey, (scope, idempotency_key), populate_existing=True)
    if stored is None or stored.request_hash != request_hash:
        raise ProblemError(
            422,
            "request.idempotency_mismatch",
            "This Idempotency-Key was already used for a different request.",
        )
    if stored.status_code is None:
        raise ProblemError(
            409, "request.idempotency_in_progress", "The first request with this key has no answer."
        )
    raise IdempotentReplay(stored.status_code, stored.response)


Idempotent = Annotated[Idempotency, Depends(idempotency)]


async def _replay(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, IdempotentReplay)
    return JSONResponse(exc.body, status_code=exc.status_code, headers={REPLAY_HEADER: "true"})


def register_idempotency(app: FastAPI) -> None:
    app.add_exception_handler(IdempotentReplay, _replay)


async def forget_scope(session: AsyncSession, scope: str) -> int:
    """Delete every stored key and response of one caller (a purged account, M1 §3.5)."""
    deleted = await session.execute(delete(IdempotencyKey).where(IdempotencyKey.scope == scope))
    return deleted.rowcount  # pyright: ignore[reportAttributeAccessIssue]
