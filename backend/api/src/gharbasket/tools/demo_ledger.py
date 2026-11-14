"""The MS-A demo's ledger proof for one customer (docs/demo/ms-a.md): read-only.

    infisical run --env=staging -- just demo-ledger +919800012345

Prints the customer's orders with their timeline, every journal entry that touches their wallet
with all of its legs (so the other side of each movement shows: Razorpay clearing for a top-up,
unearned revenue for a paid box), the wallet balance, and the ledger-wide checks: every entry
sums to zero, and so do all postings together. The transaction is READ ONLY, so it can point at
the shared staging database without the seed's confirmations; it prints the host, never the URL.
"""

import argparse
import asyncio
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncConnection

from gharbasket.platform.db import create_engine
from gharbasket.platform.settings import DatabaseSettings

REPO_ROOT = Path(__file__).resolve().parents[5]
IST = ZoneInfo("Asia/Kolkata")
PHONE = re.compile(r"^\+[1-9][0-9]{7,14}$")

ORDERS = text("""
    SELECT o.id, o.kind, o.delivery_date, o.status, o.total_paise, o.cutoff_at
    FROM orders.orders o
    JOIN identity.households h ON h.id = o.household_id
    WHERE h.owner_user_id = :user
    ORDER BY o.delivery_date DESC, o.created_at DESC
    LIMIT :limit
""")
TIMELINE = text("""
    SELECT at, from_status, to_status, actor FROM orders.order_events
    WHERE order_id = :order ORDER BY at, id
""")
ENTRIES = text("""
    SELECT e.id, e.kind, e.idempotency_key, e.created_at
    FROM billing.journal_entries e
    WHERE e.id IN (
        SELECT p.entry_id FROM billing.postings p
        JOIN billing.ledger_accounts a ON a.id = p.account_id
        WHERE a.owner_id = :user AND a.code = 'wallet'
    )
    ORDER BY e.created_at, e.id
""")
LEGS = text("""
    SELECT a.code, a.owner_type, p.amount FROM billing.postings p
    JOIN billing.ledger_accounts a ON a.id = p.account_id
    WHERE p.entry_id = :entry ORDER BY p.amount DESC, a.code
""")
BALANCE = text("""
    SELECT coalesce(sum(p.amount), 0) FROM billing.postings p
    JOIN billing.ledger_accounts a ON a.id = p.account_id
    WHERE a.owner_id = :user AND a.code = 'wallet'
""")
UNBALANCED = text("""
    SELECT count(*) FROM (
        SELECT entry_id FROM billing.postings GROUP BY entry_id HAVING sum(amount) <> 0
    ) bad
""")
TOTALS = text("""
    SELECT a.currency, count(DISTINCT p.entry_id), coalesce(sum(p.amount), 0)
    FROM billing.postings p JOIN billing.ledger_accounts a ON a.id = p.account_id
    GROUP BY a.currency ORDER BY a.currency
""")


def rupees(paise: int) -> str:
    """Indian grouping: ₹1,23,456.07."""
    sign = "-" if paise < 0 else ""
    whole, part = divmod(abs(paise), 100)
    digits = str(whole)
    head, tail = digits[:-3], digits[-3:]
    while len(head) > 2:
        tail = f"{head[-2:]},{tail}"
        head = head[:-2]
    grouped = f"{head},{tail}" if head else tail
    return f"{sign}₹{grouped}.{part:02d}"


def leg(code: str, owner_type: str, amount: int) -> str:
    """A wallet is a liability: the ledger stores debits as + and credits as -, so a credit to the
    customer's wallet (money the customer can spend) is a negative amount."""
    side = "debit " if amount > 0 else "credit"
    name = code if owner_type == "platform" else f"{code} (customer)"
    return f"      {side} {name:<28} {rupees(abs(amount)):>12}"


async def report(conn: AsyncConnection, phone: str, limit: int) -> int:
    """Print the report; 0 when the ledger balances, 1 for an unknown phone, 2 when it does not."""
    user = await conn.scalar(
        text("SELECT id FROM identity.users WHERE phone_e164 = :phone"), {"phone": phone}
    )
    if user is None:
        print(f"No user with phone {phone}.")
        return 1

    print(f"Orders (latest {limit}; times IST)")
    orders = (await conn.execute(ORDERS, {"user": user, "limit": limit})).all()
    if not orders:
        print("  none")
    for order_id, kind, day, status, total, cutoff_at in orders:
        cutoff = cutoff_at.astimezone(IST)
        print(f"  {kind} for {day}: {status}, total {rupees(total)}, cut-off {cutoff:%d %b %H:%M}")
        for at, before, after, actor in (await conn.execute(TIMELINE, {"order": order_id})).all():
            moved = at.astimezone(IST)
            print(f"    {moved:%d %b %H:%M:%S} {before or '-':>10} -> {after:<10} by {actor}")

    print("\nJournal entries on the customer's wallet (every leg; each entry sums to zero)")
    entries = (await conn.execute(ENTRIES, {"user": user})).all()
    if not entries:
        print("  none")
    for entry_id, kind, key, created_at in entries:
        legs = (await conn.execute(LEGS, {"entry": entry_id})).all()
        total = sum(amount for _, _, amount in legs)
        print(f"  {created_at.astimezone(IST):%d %b %H:%M:%S} {kind} [{key}]  sum {total}")
        for code, owner_type, amount in legs:
            print(leg(code, owner_type, amount))

    balance = -int(await conn.scalar(BALANCE, {"user": user}) or 0)
    print(f"\nWallet balance: {rupees(balance)}")

    unbalanced = int(await conn.scalar(UNBALANCED) or 0)
    print("\nWhole ledger")
    print(f"  entries that do not sum to zero: {unbalanced}")
    totals: list[Any] = list((await conn.execute(TOTALS)).all())
    for currency, count, total in totals:
        print(f"  {currency}: {count} entries, all postings sum to {total}")
    ok = unbalanced == 0 and all(total == 0 for _, _, total in totals)
    print("  balanced" if ok else "  NOT BALANCED")
    return 0 if ok else 2


async def run(url: str, phone: str, limit: int) -> int:
    engine = create_engine(url, pooled=False)
    try:
        async with engine.connect() as conn, conn.begin():
            await conn.execute(text("SET TRANSACTION READ ONLY"))  # first, so nothing can write
            return await report(conn, phone, limit)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only ledger proof for one customer.")
    parser.add_argument("phone", help="E.164, e.g. +919800012345")
    parser.add_argument("--orders", type=int, default=3, help="latest orders to show (default 3)")
    args = parser.parse_args(argv)
    if not PHONE.match(args.phone):
        parser.error("phone must be E.164, e.g. +919800012345")
    settings = DatabaseSettings(_env_file=REPO_ROOT / ".env")  # pyright: ignore[reportCallIssue]
    url = settings.migrations_url
    if url is None:
        parser.error("set DATABASE_URL (infisical run --env=staging -- …)")
    print(f"Database host: {make_url(url).host} (read only)\n")
    return asyncio.run(run(url, args.phone, args.orders))


if __name__ == "__main__":
    raise SystemExit(main())
