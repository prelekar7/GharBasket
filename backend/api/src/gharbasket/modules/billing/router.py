"""HTTP endpoints of M5 Billing & Ledger: wallet, top-ups and the Razorpay webhook (spec §3.1), and
the customer statement (§3.2)."""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.dialects.postgresql import insert

from gharbasket.modules.billing import jobs as _jobs  # noqa: F401 (registers the job, consumer)
from gharbasket.modules.billing import kindness, statements, topups, wallet
from gharbasket.modules.billing import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.billing.models import Payment, PaymentWebhook
from gharbasket.modules.billing.razorpay import (
    RazorpayClient,
    payments_unavailable,
    valid_signature,
)
from gharbasket.modules.billing.schemas import (
    DevPayIn,
    DevPayOut,
    DonationIn,
    DonationOut,
    Entry,
    KindnessBalanceOut,
    KindnessGoal,
    OrderRef,
    PointsEntry,
    PointsExpiring,
    PointsRef,
    StatementEntry,
    StatementOut,
    TopUpIn,
    TopUpOut,
    TopUpRef,
    WalletOut,
    WebhookAck,
)
from gharbasket.modules.billing.wallet import CAP_PAISE, WalletEntry
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.pagination import Cursor, Limit, Page
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.rate_limit import enforce
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import Valkey

router = APIRouter(tags=["billing"])

CanRead = Annotated[Permit, require("wallet:read")]
CanWrite = Annotated[Permit, require("wallet:write")]
CanReadStatements = Annotated[Permit, require("statements:read")]
CanReadKindness = Annotated[Permit, require("kindness:read")]
CanDonate = Annotated[Permit, require("kindness:donate")]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_razorpay(request: Request) -> RazorpayClient | None:
    """Built at startup when RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET are set (tests set a fake)."""
    return getattr(request.app.state, "razorpay", None)


Razorpay = Annotated[RazorpayClient | None, Depends(get_razorpay)]


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


@router.get(
    "/statements",
    summary="The caller's wallet for one month (IST), from the ledger, with running balances",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden"),
        422: _problem("billing.period_invalid: not YYYY-MM, or after the current month (IST)"),
    },
)
async def get_statement(
    permit: CanReadStatements,
    session: DbSession,
    response: Response,
    period: Annotated[str, Query(max_length=16, examples=["2026-10"])],
) -> StatementOut:
    month = statements.parse_period(period)
    found = await statements.statement(session, permit.principal.user_id, month)
    response.headers["Cache-Control"] = "no-store"
    return StatementOut(
        period=month.period,
        currency="INR",
        opening_paise=found.opening_paise,
        credits_paise=found.credits_paise,
        debits_paise=found.debits_paise,
        closing_paise=found.closing_paise,
        entries=[
            StatementEntry(
                **_entry(line.row).model_dump(), balance_after_paise=line.balance_after_paise
            )
            for line in found.lines
        ],
        generated_at=datetime.now(UTC),
    )


def _topup(payment: Payment, key_id: str) -> TopUpOut:
    return TopUpOut(
        id=payment.id,
        razorpay_order_id=payment.provider_order_id or "",
        key_id=key_id,
        amount_paise=payment.amount_paise,
        currency="INR",
        status=topups.status_of(payment),  # pyright: ignore[reportArgumentType]
        created_at=payment.created_at,
    )


@router.post(
    "/wallet/topups",
    status_code=201,
    summary="Start a top-up: a Razorpay order for Checkout (the webhook credits the wallet)",
    responses={
        422: _problem(
            "billing.topup_too_small, billing.wallet_limit or billing.monthly_topup_limit "
            "(with room_paise), request.invalid"
        ),
        429: _problem("billing.too_many_topups"),
        503: _problem("billing.payments_unavailable"),
    },
)
async def create_topup(
    body: TopUpIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    valkey: Valkey,
    settings: AppSettings,
    razorpay: Razorpay,
) -> TopUpOut:
    user_id = permit.principal.user_id
    await enforce(
        valkey,
        f"topup:{user_id}",
        limit=10,
        window_s=3600,
        code="billing.too_many_topups",
        detail="Too many top-ups started. Try again in an hour.",
    )
    created = await topups.create_topup(
        session, user_id, body.amount_paise, razorpay, dev_orders=settings.is_local
    )
    out = _topup(created.payment, created.key_id)
    await idem.save(201, out)
    await session.commit()
    return out


@router.get(
    "/wallet/topups/{topup_id}",
    summary="A top-up's status: poll it after Checkout until it is paid",
    responses={404: _problem("billing.topup_not_found")},
)
async def get_topup(
    topup_id: uuid.UUID,
    permit: CanRead,
    session: DbSession,
    response: Response,
    settings: AppSettings,
) -> TopUpOut:
    payment = await topups.get_topup(session, permit.principal.user_id, topup_id)
    response.headers["Cache-Control"] = "no-store"
    key_id = topups.DEV_KEY_ID if payment.provider == "dev" else settings.razorpay_key_id
    return _topup(payment, key_id or "")


@router.post(
    "/payments/webhook/razorpay",
    summary="Razorpay's webhook (signed); not for the apps",
    responses={
        400: _problem("request.invalid: not JSON"),
        401: _problem("billing.bad_signature"),
        503: _problem("billing.payments_unavailable: no webhook secret configured"),
    },
)
async def razorpay_webhook(
    request: Request,
    session: DbSession,
    settings: AppSettings,
    razorpay: Razorpay,
    x_razorpay_signature: Annotated[str, Header(max_length=256)],
    x_razorpay_event_id: Annotated[str, Header(min_length=1, max_length=80)],
) -> WebhookAck:
    secret = settings.razorpay_webhook_secret
    if secret is None:
        raise payments_unavailable()
    body = await request.body()
    if not valid_signature(body, x_razorpay_signature, secret.get_secret_value()):
        raise ProblemError(401, "billing.bad_signature", "The webhook signature is not valid.")
    try:
        event = json.loads(body)
    except ValueError as exc:
        raise ProblemError(400, "request.invalid", "The webhook body is not JSON.") from exc
    if not isinstance(event, dict):
        raise ProblemError(400, "request.invalid", "The webhook body is not a JSON object.")
    # Delivered at least once: the event id is stored once, and a repeat does nothing.
    stored = (
        await session.execute(
            insert(PaymentWebhook)
            .values(
                provider="razorpay",
                event_id=x_razorpay_event_id,
                event=str(event.get("event", ""))[:80],
                payload=event,
            )
            .on_conflict_do_nothing(index_elements=["event_id"])
            .returning(PaymentWebhook.id)
        )
    ).scalar_one_or_none()
    if stored is None:
        return WebhookAck(outcome="duplicate")
    outcome = await topups.handle_event(session, event, razorpay)
    webhook = await session.get(PaymentWebhook, stored)
    if webhook is not None:
        webhook.outcome = outcome
        webhook.processed_at = datetime.now(UTC)
    await session.commit()
    return WebhookAck(outcome=outcome)


@router.post(
    "/dev/razorpay/pay",
    include_in_schema=False,  # developer machines and tests only (spec §3.1)
)
async def dev_pay(
    body: DevPayIn,
    permit: CanWrite,
    session: DbSession,
    settings: AppSettings,
    razorpay: Razorpay,
) -> DevPayOut:
    """Stands in for Razorpay Checkout plus its webhook: the same processing as a real event."""
    if not settings.is_local:
        raise ProblemError(404, "http.not_found", "Not Found")  # as if the route didn't exist
    payment = await topups.by_order(session, body.razorpay_order_id)
    if payment is None or payment.user_id != permit.principal.user_id:
        raise topups.topup_not_found()
    event = topups.dev_capture(payment)
    await topups.handle_event(session, event, razorpay)
    await session.commit()
    return DevPayOut(
        razorpay_payment_id=event["payload"]["payment"]["entity"]["id"], status="captured"
    )


# --- Kindness points (§3.6) ---------------------------------------------------------------------


def _points_entry(row: kindness.PointsEntry) -> PointsEntry:
    e = row.entry
    ref = None
    if e.ref_type in ("donation", "referral") and e.ref_id is not None:
        ref = PointsRef(type=e.ref_type, id=e.ref_id)  # pyright: ignore[reportArgumentType]
    return PointsEntry(
        id=e.id,
        kind=e.kind,  # pyright: ignore[reportArgumentType]
        points=row.points,
        ref=ref,
        expires_on=row.expires_on,
        at=e.created_at,
    )


@router.get(
    "/kindness/balance",
    summary="The caller's Kindness points, the next expiry dates and the next meal's goal",
    responses={401: _problem("auth.invalid_token"), 403: _problem("auth.forbidden")},
)
async def get_kindness_balance(
    permit: CanReadKindness, session: DbSession, response: Response, settings: AppSettings
) -> KindnessBalanceOut:
    found = await kindness.balance(session, permit.principal.user_id)
    response.headers["Cache-Control"] = "no-store"
    return KindnessBalanceOut(
        points=found.points,
        expiring=[PointsExpiring(points=e.points, on=e.on) for e in found.expiring],
        goal=KindnessGoal(points=settings.kindness_goal, code=kindness.GOAL_CODE),
    )


@router.get(
    "/kindness/entries",
    summary="The caller's Kindness points history, newest first",
    responses={401: _problem("auth.invalid_token"), 403: _problem("auth.forbidden")},
)
async def list_kindness_entries(
    permit: CanReadKindness,
    session: DbSession,
    response: Response,
    cursor: Cursor = None,
    limit: Limit = 50,
) -> Page[PointsEntry]:
    rows, next_cursor = await kindness.entries(session, permit.principal.user_id, cursor, limit)
    response.headers["Cache-Control"] = "no-store"
    return Page(items=[_points_entry(row) for row in rows], next_cursor=next_cursor)


@router.post(
    "/kindness/donations",
    status_code=201,
    summary="Give Kindness points as donated meals (a meal for a child); the only way to use them",
    responses={
        409: _problem("billing.not_enough_points (with points and needed)"),
        422: _problem("request.invalid: meals is 1 to 10"),
    },
)
async def donate_meals(
    body: DonationIn,
    permit: CanDonate,
    session: DbSession,
    idem: Idempotent,
    settings: AppSettings,
) -> DonationOut:
    goal = settings.kindness_goal
    entry = await kindness.donate(session, permit.principal.user_id, body.meals, goal, idem.key)
    out = DonationOut(
        id=entry.id,
        meals=body.meals,
        points=body.meals * goal,
        code=kindness.GOAL_CODE,
        at=entry.created_at,
    )
    await idem.save(201, out)
    await session.commit()
    return out
