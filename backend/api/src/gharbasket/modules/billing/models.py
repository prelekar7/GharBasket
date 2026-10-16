"""Tables of M5 Billing & Ledger. Every table sets `__table_args__ = {"schema": SCHEMA}`.

The double-entry ledger (spec §4.1, handbook 05 §5.7): accounts, journal entries and their
postings. Amounts are signed minor units, debit positive and credit negative, so the postings of
an entry sum to zero per currency; a deferred constraint trigger (see the migration) refuses any
transaction where they don't, and entries and postings can never be updated or deleted.

Also the top-up payments and the Razorpay webhooks that settle them (spec §3.1), and the lots of
Kindness points with their expiry (§3.6), and refunds of wallet money to the payments it came from
(§3.3).
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
    Integer,
    SmallInteger,
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
ENTRY_KINDS = (
    "topup",
    "topup_bonus",
    "box_debit",
    "refund",
    "adjustment",
    # Kindness points (§3.6), a separate currency (KINDNESS_PTS), never money.
    "points_earned",
    "points_expired",
    "points_donated",
    # Refunds of wallet money to the payments it came from (§3.3).
    "refund_to_source",
    "refund_to_source_failed",
    "unclaimed",
)
REF_TYPES = ("topup", "order", "donation", "referral", "refund")
PROVIDERS = ("razorpay", "dev")
PAYMENT_PURPOSES = ("topup",)
# Stored states; "expired" is shown for a "created" top-up past `expires_at` (spec §3.1).
PAYMENT_STATUSES = ("created", "paid", "failed", "refunded")
REFUND_REASONS = ("customer", "account_deleted")
REFUND_STATUSES = ("pending", "processing", "processed", "partly_failed", "failed")
OPEN_REFUND = "status IN ('pending', 'processing') AND reason = 'customer'"
PART_STATUSES = ("pending", "sent", "processed", "failed")
IN_FLIGHT = ("pending", "sent")


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
    kind: Mapped[str] = mapped_column(String(32))
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


class Payment(Base):
    """A wallet top-up: our record of a Razorpay order (or a dev order) and how it ended."""

    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(_one_of("provider", PROVIDERS), name="provider"),
        CheckConstraint(_one_of("purpose", PAYMENT_PURPOSES), name="purpose"),
        CheckConstraint(_one_of("status", PAYMENT_STATUSES), name="status"),
        CheckConstraint("amount_paise > 0", name="amount_paise"),
        Index(None, "user_id", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1's user; no foreign key across schemas
    provider: Mapped[str] = mapped_column(String(16))
    purpose: Mapped[str] = mapped_column(String(16), server_default="topup")
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), server_default="created")
    provider_order_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    provider_payment_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    provider_refund_id: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PaymentWebhook(Base):
    """Every webhook delivery, stored once by Razorpay's event id (they arrive at least once)."""

    __tablename__ = "payment_webhooks"
    __table_args__ = ({"schema": SCHEMA},)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    provider: Mapped[str] = mapped_column(String(16))
    event_id: Mapped[str] = mapped_column(String(80), unique=True)
    event: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    outcome: Mapped[str | None] = mapped_column(String(40))  # credited, ignored, unknown_order…
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PointsLot(Base):
    """One credit of Kindness points and what is left of it: the expiry schedule (§3.6). The
    balance is still the sum of postings to `kindness:<user>`, and Σ remaining equals it."""

    __tablename__ = "points_lots"
    __table_args__ = (
        CheckConstraint("points > 0", name="points"),
        CheckConstraint("remaining >= 0 AND remaining <= points", name="remaining"),
        UniqueConstraint("source_type", "source_id"),
        Index(None, "user_id", "expires_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1's user; no foreign key across schemas
    points: Mapped[int] = mapped_column(Integer)
    remaining: Mapped[int] = mapped_column(Integer)
    earned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # What earned them (M9): "donation", "referral" or "referral_welcome", and that row's id.
    source_type: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.journal_entries.id", ondelete="RESTRICT")
    )


class Refund(Base):
    """Wallet money going back to the payments it came from (spec §3.3), in one or more parts."""

    __tablename__ = "refunds"
    __table_args__ = (
        CheckConstraint(_one_of("reason", REFUND_REASONS), name="reason"),
        CheckConstraint(_one_of("status", REFUND_STATUSES), name="status"),
        CheckConstraint("amount_paise > 0", name="amount_paise"),
        # One customer refund in progress per user, even for two requests at the same moment.
        Index(
            "ix_billing_refunds_one_open",
            "user_id",
            unique=True,
            postgresql_where=text(OPEN_REFUND),
        ),
        Index(None, "user_id", "id"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # M1's user; no foreign key across schemas
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(255))  # none for account deletion
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefundPart(Base):
    """The share of a refund one top-up takes back: one Razorpay refund, sent by `send_refunds`."""

    __tablename__ = "refund_parts"
    __table_args__ = (
        CheckConstraint(_one_of("status", PART_STATUSES), name="status"),
        CheckConstraint("amount_paise > 0", name="amount_paise"),
        Index(None, "status", "next_attempt_at"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    refund_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.refunds.id", ondelete="RESTRICT"), index=True
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.payments.id", ondelete="RESTRICT"), index=True
    )
    # A failed part of a deleting account is moved once to another top-up (spec §3.3).
    replaces_part_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.refund_parts.id", ondelete="RESTRICT")
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    provider_refund_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    attempts: Mapped[int] = mapped_column(SmallInteger, server_default=text("0"))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(200))
    entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.journal_entries.id", ondelete="RESTRICT")
    )
    failed_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.journal_entries.id", ondelete="RESTRICT")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
