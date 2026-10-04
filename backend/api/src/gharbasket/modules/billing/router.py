"""HTTP endpoints of M5 Billing & Ledger: the wallet (spec §3.1)."""

from typing import Annotated, Any

from fastapi import APIRouter, Response

from gharbasket.modules.billing import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.billing import wallet
from gharbasket.modules.billing.schemas import Entry, OrderRef, TopUpRef, WalletOut
from gharbasket.modules.billing.wallet import CAP_PAISE, WalletEntry
from gharbasket.platform.db import DbSession
from gharbasket.platform.pagination import Cursor, Limit, Page
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["billing"])

CanRead = Annotated[Permit, require("wallet:read")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _entry(row: WalletEntry) -> Entry:
    e = row.entry
    ref: TopUpRef | OrderRef | None = None
    if e.ref_type == "topup" and e.ref_id is not None:
        ref = TopUpRef(type="topup", id=e.ref_id)
    elif e.ref_type == "order" and e.ref_id is not None:
        ref = OrderRef(
            type="order", id=e.ref_id, delivery_date=(e.details or {}).get("delivery_date")
        )
    return Entry(id=e.id, kind=e.kind, amount_paise=row.amount_paise, ref=ref, at=e.created_at)  # pyright: ignore[reportArgumentType]


@router.get(
    "/wallet",
    summary="The caller's wallet: balance, room to top up, latest entries",
    responses={401: _problem("auth.invalid_token"), 403: _problem("auth.forbidden")},
)
async def get_wallet(permit: CanRead, session: DbSession, response: Response) -> WalletOut:
    found = await wallet.summary(session, permit.principal.user_id)
    response.headers["Cache-Control"] = "no-store"
    return WalletOut(
        balance_paise=found.balance_paise,
        cap_paise=CAP_PAISE,
        topup_room_paise=found.topup_room_paise,
        entries=[_entry(row) for row in found.entries],
    )


@router.get(
    "/wallet/entries",
    summary="Older wallet entries, newest first",
    responses={401: _problem("auth.invalid_token"), 403: _problem("auth.forbidden")},
)
async def list_entries(
    permit: CanRead,
    session: DbSession,
    response: Response,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[Entry]:
    rows, next_cursor = await wallet.entries(session, permit.principal.user_id, cursor, limit)
    response.headers["Cache-Control"] = "no-store"
    return Page(items=[_entry(row) for row in rows], next_cursor=next_cursor)
