"""Tables of M5 Billing & Ledger. Every table sets `__table_args__ = {"schema": SCHEMA}`.

The double-entry ledger (spec §4.1, handbook 05 §5.7): accounts, journal entries and their
postings. Amounts are signed minor units, debit positive and credit negative, so the postings of
an entry sum to zero per currency; a deferred constraint trigger (see the migration) refuses any
transaction where they don't, and entries and postings can never be updated or deleted.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "billing"

CURRENCIES = ("INR", "KINDNESS_PTS")
ACCOUNT_KINDS = ("asset", "liability", "revenue", "expense")
OWNER_TYPES = ("platform", "user", "partner")
ENTRY_KINDS = ("topup", "topup_bonus", "box_debit", "refund", "adjustment")
REF_TYPES = ("topup", "order")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class LedgerAccount(Base):
    """One account per (code, owner, currency): `wallet` for each user, one of each platform
    account (`razorpay_clearing`, `unearned_revenue`, …) with no owner."""

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        CheckConstraint(_one_of("currency", CURRENCIES), name="currency"),
        CheckConstraint(_one_of("kind", ACCOUNT_KINDS), name="kind"),
        CheckConstraint(_one_of("owner_type", OWNER_TYPES), name="owner_type"),
        CheckConstraint("(owner_type = 'platform') = (owner_id IS NULL)", name="owner"),
        UniqueConstraint(
            "code", "owner_type", "owner_id", "currency", postgresql_nulls_not_distinct=True
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    code: Mapped[str] = mapped_column(String(40))
    owner_type: Mapped[str] = mapped_column(String(16))
    # A user or partner id from another module: no foreign key across schemas.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    currency: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    __table_args__ = (
        CheckConstraint(_one_of("kind", ENTRY_KINDS), name="kind"),
        CheckConstraint(f"ref_type IS NULL OR {_one_of('ref_type', REF_TYPES)}", name="ref_type"),
        {"schema": SCHEMA},
    )

    # uuidv7: time-ordered, so "newest first" is "id descending".
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    kind: Mapped[str] = mapped_column(String(20))
    ref_type: Mapped[str | None] = mapped_column(String(16))
    ref_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # e.g. {"delivery_date": …}
    # What caused the entry ("box:<order id>"): posting the same cause twice is a no-op.
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Posting(Base):
    __tablename__ = "postings"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount"),
        Index(None, "account_id", "entry_id"),
        {"schema": SCHEMA},
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.journal_entries.id", ondelete="RESTRICT"), primary_key=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.ledger_accounts.id", ondelete="RESTRICT"), primary_key=True
    )
    amount: Mapped[int] = mapped_column(BigInteger)  # minor units: paise or points
