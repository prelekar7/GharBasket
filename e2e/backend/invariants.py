"""Invariants that hold after any day, whatever happened in it (read straight from the tables).

Each check takes a session and returns its violations as readable lines (an empty list: it holds),
so a failure names the rows. Later waves add checks here and to `ALL`; a check whose tables are not
built yet returns None (skipped).
"""

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.wallet import CAP_PAISE
from gharbasket.modules.notifications.routes import ROUTES
from gharbasket.modules.orders import state
from gharbasket.platform.errors import ProblemError

Check = Callable[[AsyncSession], Awaitable[list[str] | None]]
# Handbook 06 §6.2: APPROVED → APPROVED is a customer's edit (M4 replaces the box lines). The
# timeline records it, though `state.TRANSITIONS` has no self-loop (nothing moves the status).
EDIT = {("approved", "approved", "customer")}


async def _rows(session: AsyncSession, sql: str, **params: Any) -> list[Any]:
    return list((await session.execute(text(sql), params)).all())


async def ledger_entries_balance(session: AsyncSession) -> list[str]:
    """Every journal entry has at least two postings and they sum to zero in each currency."""
    rows = await _rows(
        session,
        """
        SELECT e.id, e.kind, e.idempotency_key, a.currency, sum(p.amount) AS total, count(*) AS n
        FROM billing.journal_entries e
        JOIN billing.postings p ON p.entry_id = e.id
        JOIN billing.ledger_accounts a ON a.id = p.account_id
        GROUP BY e.id, e.kind, e.idempotency_key, a.currency
        HAVING sum(p.amount) <> 0
        """,
    )
    lonely = await _rows(
        session,
        """
        SELECT e.id, e.kind, count(p.entry_id) AS n FROM billing.journal_entries e
        LEFT JOIN billing.postings p ON p.entry_id = e.id
        GROUP BY e.id, e.kind HAVING count(p.entry_id) < 2
        """,
    )
    return [
        f"entry {r.id} ({r.kind}, {r.idempotency_key}) sums to {r.total} {r.currency}" for r in rows
    ] + [f"entry {r.id} ({r.kind}) has {r.n} posting(s)" for r in lonely]


async def ledger_accounts_consistent(session: AsyncSession) -> list[str]:
    """The books add up across accounts: all postings sum to zero per currency; the platform's
    clearing and unearned-revenue accounts equal the entries that move money through them; and
    every wallet equals its top-ups minus its box debits plus its refunds and adjustments."""
    out: list[str] = []
    for r in await _rows(
        session,
        """
        SELECT a.currency, sum(p.amount) AS total FROM billing.postings p
        JOIN billing.ledger_accounts a ON a.id = p.account_id GROUP BY a.currency
        HAVING sum(p.amount) <> 0
        """,
    ):
        out.append(f"all {r.currency} postings sum to {r.total}, not 0")
    by_kind = {
        (r.code, r.kind): int(r.total)
        for r in await _rows(
            session,
            """
            SELECT a.code, e.kind, sum(p.amount) AS total FROM billing.postings p
            JOIN billing.ledger_accounts a ON a.id = p.account_id
            JOIN billing.journal_entries e ON e.id = p.entry_id
            WHERE a.owner_type = 'platform' AND a.currency = 'INR' GROUP BY a.code, e.kind
            """,
        )
    }
    wallet_kinds = {
        r.kind: -int(r.total)
        for r in await _rows(
            session,
            """
            SELECT e.kind, sum(p.amount) AS total FROM billing.postings p
            JOIN billing.ledger_accounts a ON a.id = p.account_id
            JOIN billing.journal_entries e ON e.id = p.entry_id
            WHERE a.code = 'wallet' AND a.owner_type = 'user' GROUP BY e.kind
            """,
        )
    }
    topups = wallet_kinds.get("topup", 0)
    if by_kind.get(("razorpay_clearing", "topup"), 0) != topups:
        out.append(
            f"razorpay_clearing took {by_kind.get(('razorpay_clearing', 'topup'), 0)} paise of "
            f"top-ups but wallets were credited {topups}"
        )
    debits, refunds = -wallet_kinds.get("box_debit", 0), wallet_kinds.get("refund", 0)
    unearned = -sum(v for (code, _), v in by_kind.items() if code == "unearned_revenue")
    if unearned != debits - refunds:
        out.append(
            f"unearned_revenue holds {unearned} paise; box debits {debits} - refunds {refunds} "
            f"= {debits - refunds}"
        )
    # Each wallet against its own entries, entry by entry (a posting on someone else's wallet
    # under this user's entry would show here).
    for r in await _rows(
        session,
        """
        SELECT a.owner_id,
               -sum(p.amount) AS balance,
               -sum(p.amount) FILTER (WHERE e.kind IN ('topup', 'refund', 'adjustment',
                                                      'topup_bonus',
                                                      'refund_to_source_failed')) AS credits,
               sum(p.amount) FILTER (WHERE e.kind IN ('box_debit', 'refund_to_source',
                                                      'unclaimed')) AS debits
        FROM billing.ledger_accounts a
        JOIN billing.postings p ON p.account_id = a.id
        JOIN billing.journal_entries e ON e.id = p.entry_id
        WHERE a.code = 'wallet' AND a.owner_type = 'user' AND a.currency = 'INR'
        GROUP BY a.owner_id
        """,
    ):
        credits, debit = int(r.credits or 0), int(r.debits or 0)
        if int(r.balance) != credits - debit:
            out.append(
                f"wallet of {r.owner_id}: balance {r.balance} ≠ credits {credits} - debits {debit}"
            )
    # Money in matches what the payment provider captured (dev or Razorpay top-ups).
    for r in await _rows(
        session,
        """
        SELECT pay.id, pay.amount_paise, pay.status, count(e.id) AS entries,
               coalesce(-sum(p.amount) FILTER (WHERE a.code = 'wallet'), 0) AS credited
        FROM billing.payments pay
        LEFT JOIN billing.journal_entries e ON e.ref_type = 'topup' AND e.ref_id = pay.id
                                             AND e.kind = 'topup'
        LEFT JOIN billing.postings p ON p.entry_id = e.id
        LEFT JOIN billing.ledger_accounts a ON a.id = p.account_id
        GROUP BY pay.id, pay.amount_paise, pay.status
        """,
    ):
        expected = r.amount_paise if r.status in ("paid", "refunded") else 0
        if r.status == "paid" and int(r.credited) != expected:
            out.append(f"payment {r.id} paid {r.amount_paise} but credited {r.credited}")
        if r.status in ("created", "failed") and int(r.credited) != 0:
            out.append(f"payment {r.id} is {r.status} but credited {r.credited}")
    return out


async def wallets_within_bounds(session: AsyncSession) -> list[str]:
    """No wallet below zero or above the ₹10,000 cap."""
    rows = await _rows(
        session,
        """
        SELECT a.owner_id, -coalesce(sum(p.amount), 0) AS balance FROM billing.ledger_accounts a
        LEFT JOIN billing.postings p ON p.account_id = a.id
        WHERE a.code = 'wallet' AND a.owner_type = 'user' AND a.currency = 'INR'
        GROUP BY a.owner_id
        HAVING -coalesce(sum(p.amount), 0) < 0 OR -coalesce(sum(p.amount), 0) > :cap
        """,
        cap=CAP_PAISE,
    )
    return [f"wallet of {r.owner_id} holds {r.balance} paise" for r in rows]


async def orders_follow_their_state_machine(session: AsyncSession) -> list[str]:
    """Every order's status is one its kind can have, and its timeline is a legal walk through
    the kind's table (ADR-0026 §2/§3, ADR-0027): it starts at `approved`, each move starts where
    the last ended, the actor may make it, and it ends at the order's status."""
    out: list[str] = []
    orders = {r.id: r for r in await _rows(session, "SELECT id, kind, status FROM orders.orders")}
    events: dict[Any, list[Any]] = {}
    for e in await _rows(
        session,
        "SELECT order_id, from_status, to_status, actor FROM orders.order_events ORDER BY id",
    ):
        events.setdefault(e.order_id, []).append(e)
    for order_id, order in orders.items():
        table = state.TRANSITIONS.get(order.kind)
        if table is None:
            out.append(f"order {order_id}: unknown kind {order.kind}")
            continue
        if order.status not in table:
            out.append(
                f"order {order_id} ({order.kind}): status {order.status} is not in its table"
            )
        timeline = events.get(order_id, [])
        if not timeline:
            out.append(f"order {order_id}: no timeline")
            continue
        first, *moves = timeline
        if (first.from_status, first.to_status) != (None, "approved"):
            out.append(f"order {order_id}: starts {first.from_status} → {first.to_status}")
        at = first.to_status
        for move in moves:
            if move.from_status != at:
                out.append(f"order {order_id}: {move.from_status} → {move.to_status} after {at}")
            if (move.from_status, move.to_status, move.actor) in EDIT:
                continue  # a customer's edit before the cut-off: the lines change, not the status
            try:
                state.check(order.kind, move.from_status, move.to_status, move.actor)
            except ProblemError as illegal:
                out.append(f"order {order_id}: {illegal.detail} (actor {move.actor})")
            at = move.to_status
        if at != order.status:
            out.append(f"order {order_id}: the timeline ends at {at}, the order is {order.status}")
    return out


async def paid_orders_debited_once(session: AsyncSession) -> list[str]:
    """No idempotency key twice; at most one box debit per order, for its total; every paid
    order has one, and an order that was never paid has none."""
    out = [
        f"idempotency key {r.idempotency_key} has {r.n} entries"
        for r in await _rows(
            session,
            """
            SELECT idempotency_key, count(*) AS n FROM billing.journal_entries
            GROUP BY idempotency_key HAVING count(*) > 1
            """,
        )
    ]
    debits: dict[Any, list[int]] = {}
    for r in await _rows(
        session,
        """
        SELECT e.ref_id, sum(p.amount) AS amount FROM billing.journal_entries e
        JOIN billing.postings p ON p.entry_id = e.id
        JOIN billing.ledger_accounts a ON a.id = p.account_id AND a.code = 'wallet'
        WHERE e.kind = 'box_debit' GROUP BY e.id, e.ref_id
        """,
    ):
        debits.setdefault(r.ref_id, []).append(int(r.amount))
    ever_paid = {
        r.order_id
        for r in await _rows(
            session, "SELECT DISTINCT order_id FROM orders.order_events WHERE to_status = 'locked'"
        )
    }
    for order in await _rows(session, "SELECT id, status, total_paise FROM orders.orders"):
        taken = debits.pop(order.id, [])
        if len(taken) > 1:
            out.append(f"order {order.id} was debited {len(taken)} times: {taken}")
        if order.id in ever_paid and taken != [order.total_paise]:
            out.append(
                f"order {order.id} ({order.status}) total {order.total_paise}, debited {taken}"
            )
        if order.id not in ever_paid and taken:
            out.append(f"order {order.id} ({order.status}) never locked but debited {taken}")
    out += [f"box debit for {ref} which is no order" for ref in debits]
    return out


async def outbox_fully_relayed(session: AsyncSession) -> list[str]:
    """Every outbox event went out (none pending, none stuck failing)."""
    rows = await _rows(
        session,
        """
        SELECT event_type, count(*) AS n, max(attempts) AS attempts, max(last_error) AS error
        FROM platform.outbox WHERE sent_at IS NULL GROUP BY event_type
        """,
    )
    return [
        f"{r.n} x {r.event_type} not relayed (attempts {r.attempts}, last error {r.error})"
        for r in rows
    ]


async def one_message_per_event_and_person(session: AsyncSession) -> list[str]:
    """M11 writes at most one message per (event, person), and one delivery per channel."""
    messages = await _rows(
        session,
        """
        SELECT event_id, user_id, count(*) AS n FROM notifications.messages
        GROUP BY event_id, user_id HAVING count(*) > 1
        """,
    )
    deliveries = await _rows(
        session,
        """
        SELECT message_id, channel, count(*) AS n FROM notifications.deliveries
        GROUP BY message_id, channel HAVING count(*) > 1
        """,
    )
    return [f"event {r.event_id} → user {r.user_id}: {r.n} messages" for r in messages] + [
        f"message {r.message_id}: {r.n} {r.channel} deliveries" for r in deliveries
    ]


async def routed_events_have_their_message(
    session: AsyncSession, *, after: Any = None
) -> list[str]:
    """Every relayed event that M11 maps to a message has one for each person its route names
    (M11 §3.1's event map, asked of the routes themselves); `after`: only events newer than
    that outbox id. A payload the route cannot read is dropped by design, so it is skipped."""
    events = await _rows(
        session,
        """
        SELECT id, event_type, payload FROM platform.outbox
        WHERE sent_at IS NOT NULL AND event_type = ANY(:types)
          AND (CAST(:after AS uuid) IS NULL OR id > CAST(:after AS uuid))
        ORDER BY id
        """,
        types=list(ROUTES),
        after=str(after) if after else None,
    )
    have = {
        (r.event_id, r.user_id)
        for r in await _rows(session, "SELECT event_id, user_id FROM notifications.messages")
    }
    missing: dict[str, int] = {}
    first: dict[str, str] = {}
    for event in events:
        try:
            route = await ROUTES[event.event_type](session, event.payload)
        except (KeyError, ValueError, TypeError):
            continue
        for user_id in route.user_ids if route else []:
            if (event.id, user_id) not in have:
                missing[event.event_type] = missing.get(event.event_type, 0) + 1
                first.setdefault(event.event_type, f"event {event.id} → user {user_id}")
    return [
        f"{n} x {kind} relayed without its message (first: {first[kind]})"
        for kind, n in sorted(missing.items())
    ]


async def stock_equals_its_moves(session: AsyncSession) -> list[str] | None:
    """Every lot's `qty_on_hand` is the sum of its stock moves and never below zero (M6 §3.4).
    None until the stock tables exist."""
    present = await _rows(
        session,
        """
        SELECT count(*) AS n FROM information_schema.tables
        WHERE table_schema = 'hub_ops' AND table_name IN ('inventory_lots', 'stock_moves')
        """,
    )
    if present[0].n < 2:
        return None
    rows = await _rows(
        session,
        """
        SELECT l.id, l.qty_on_hand, coalesce(sum(m.qty), 0) AS moved FROM hub_ops.inventory_lots l
        LEFT JOIN hub_ops.stock_moves m ON m.lot_id = l.id
        GROUP BY l.id, l.qty_on_hand
        HAVING l.qty_on_hand <> coalesce(sum(m.qty), 0) OR l.qty_on_hand < 0
        """,
    )
    return [f"lot {r.id}: on hand {r.qty_on_hand}, moves sum to {r.moved}" for r in rows]


async def cycles_match_their_orders(session: AsyncSession) -> list[str]:
    """M3 and M4 agree on every approved week: the cycle points at its live order, mirrors its
    status (approved, payment_failed, or locked once paid) and holds the same lines at the same
    prices; an order cancelled for non-payment leaves its cycle cancelled (M3 §3.3)."""
    out: list[str] = []
    mirror = {"approved": ("approved",), "payment_failed": ("payment_failed",)}
    paid = {status: ("locked",) for status in state.PAID}
    rows = await _rows(
        session,
        """
        SELECT o.id, o.status, o.kind, o.cycle_id, c.status AS cycle_status, c.order_id,
               o.subtotal_paise
        FROM orders.orders o JOIN subscriptions.weekly_cycles c ON c.id = o.cycle_id
        WHERE o.kind = 'box'
        """,
    )
    for r in rows:
        if r.status == "cancelled":
            if r.order_id == r.id and r.cycle_status not in ("cancelled", "skipped"):
                out.append(f"order {r.id} cancelled but its cycle {r.cycle_id} is {r.cycle_status}")
            continue
        if r.order_id != r.id:
            out.append(f"cycle {r.cycle_id} points at {r.order_id}, its live order is {r.id}")
        allowed = {**mirror, **paid}.get(r.status, ())
        if r.cycle_status not in allowed:
            out.append(f"order {r.id} is {r.status} but cycle {r.cycle_id} is {r.cycle_status}")
    lines = await _rows(
        session,
        """
        SELECT o.id, c.id AS cycle_id,
               (SELECT array_agg(ol.product_id || ':' || ol.qty || '@' || ol.unit_price_paise
                                 ORDER BY ol.product_id)
                FROM orders.order_lines ol WHERE ol.order_id = o.id AND ol.kind = 'box') AS ordered,
               (SELECT array_agg(bl.product_id || ':' || bl.qty || '@' || bl.unit_price_paise
                                 ORDER BY bl.product_id)
                FROM subscriptions.box_lines bl WHERE bl.cycle_id = c.id) AS boxed
        FROM orders.orders o JOIN subscriptions.weekly_cycles c ON c.order_id = o.id
        WHERE o.kind = 'box' AND o.status <> 'cancelled'
        """,
    )
    for r in lines:
        if r.ordered != r.boxed:
            out.append(f"order {r.id} lines {r.ordered} ≠ cycle {r.cycle_id} lines {r.boxed}")
    return out


ALL: dict[str, Check] = {
    "ledger_entries_balance": ledger_entries_balance,
    "ledger_accounts_consistent": ledger_accounts_consistent,
    "wallets_within_bounds": wallets_within_bounds,
    "orders_follow_their_state_machine": orders_follow_their_state_machine,
    "paid_orders_debited_once": paid_orders_debited_once,
    "cycles_match_their_orders": cycles_match_their_orders,
    "outbox_fully_relayed": outbox_fully_relayed,
    "one_message_per_event_and_person": one_message_per_event_and_person,
    "routed_events_have_their_message": routed_events_have_their_message,
    "stock_equals_its_moves": stock_equals_its_moves,
}
