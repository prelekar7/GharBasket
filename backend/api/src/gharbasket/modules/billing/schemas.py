"""Request and response models of M5 Billing & Ledger.

The wallet shapes are the S2 contract (docs/modules/M5-billing.md §3.1), the statement is §3.2 and
refunds to source are §3.3, reconciliation is §3.4:
changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

EntryKind = Literal[
    "topup",
    "topup_bonus",
    "box_debit",
    "refund",
    "adjustment",
    "refund_to_source",
    "refund_to_source_failed",
    "unclaimed",
]


class TopUpRef(BaseModel):
    type: Literal["topup"]
    id: uuid.UUID


class OrderRef(BaseModel):
    type: Literal["order"]
    id: uuid.UUID
    delivery_date: date | None


class RefundRef(BaseModel):
    type: Literal["refund"]
    id: uuid.UUID


class Entry(BaseModel):
    id: uuid.UUID
    kind: EntryKind = Field(description="Show a generic label for a kind the app doesn't know")
    amount_paise: int = Field(description="Into the wallet +, out of it -", examples=[-69000])
    ref: Annotated[TopUpRef | OrderRef | RefundRef, Field(discriminator="type")] | None
    at: datetime


class WalletOut(BaseModel):
    balance_paise: int = Field(examples=[131000])
    cap_paise: int = Field(examples=[1000000])
    topup_room_paise: int = Field(
        description="What can still be added now: the cap minus the balance and unpaid top-ups, "
        "limited by this month's allowance",
        examples=[869000],
    )
    refundable_paise: int = Field(
        description="What POST /v1/wallet/refund-to-source can send back now", examples=[41000]
    )
    entries: list[Entry] = Field(description="The latest 20, newest first")


class StatementEntry(Entry):
    balance_after_paise: int = Field(
        description="The wallet's balance after this entry, in posting order", examples=[181000]
    )


class StatementOut(BaseModel):
    period: str = Field(examples=["2026-10"])
    currency: Literal["INR"]
    opening_paise: int = Field(description="The balance at 00:00 IST on the 1st", examples=[50000])
    credits_paise: int = Field(description="Σ positive entries in the month", examples=[200000])
    debits_paise: int = Field(
        description="Σ negative entries (signed, as on entries)", examples=[-69000]
    )
    closing_paise: int = Field(
        description="opening + credits + debits; for the current month, now", examples=[181000]
    )
    entries: list[StatementEntry] = Field(description="Every entry of the month, newest first")
    generated_at: datetime


TopUpStatus = Literal["created", "paid", "failed", "expired", "refunded"]


class TopUpIn(BaseModel):
    amount_paise: int = Field(
        ge=1, le=10_000_000, description="₹100 to ₹10,000 in whole rupees", examples=[200000]
    )


class TopUpOut(BaseModel):
    id: uuid.UUID
    razorpay_order_id: str = Field(examples=["order_EKwxwAgItmmXdp"])
    key_id: str = Field(description="Pass to Razorpay Checkout as `key`", examples=["rzp_test_x"])
    amount_paise: int
    currency: Literal["INR"]
    status: TopUpStatus
    created_at: datetime


class DevPayIn(BaseModel):
    razorpay_order_id: str = Field(max_length=64)


class DevPayOut(BaseModel):
    razorpay_payment_id: str
    status: Literal["captured"]


class WebhookAck(BaseModel):
    outcome: str = Field(description="What the event did (for Razorpay's dashboard and logs)")


# --- Kindness points (§3.6): a separate currency, never money -----------------------------------

PointsKind = Literal["points_earned", "points_expired", "points_donated"]


class PointsExpiring(BaseModel):
    points: int = Field(examples=[120])
    on: date = Field(description="The IST date these points expire at the end of")


class KindnessGoal(BaseModel):
    points: int = Field(description="The points one donated meal takes", examples=[500])
    code: Literal["child_meal"]


class KindnessBalanceOut(BaseModel):
    points: int = Field(description="Points that can be donated now", examples=[340])
    expiring: list[PointsExpiring] = Field(
        description="Soonest first, the next 3 dates within 90 days; [] if none"
    )
    goal: KindnessGoal | None = Field(description="What the next donated meal needs")


class PointsRef(BaseModel):
    type: Literal["donation", "referral"]
    id: uuid.UUID


class PointsEntry(BaseModel):
    id: uuid.UUID
    kind: PointsKind = Field(description="Show a generic label for a kind the app doesn't know")
    points: int = Field(description="Into the balance +, out of it -", examples=[120])
    ref: PointsRef | None = Field(description="What earned the points (earned and expired)")
    expires_on: date | None = Field(description="IST, for points_earned")
    at: datetime


class DonationIn(BaseModel):
    meals: int = Field(ge=1, le=10, examples=[1])


class DonationOut(BaseModel):
    id: uuid.UUID = Field(description="The journal entry")
    meals: int = Field(examples=[1])
    points: int = Field(examples=[500])
    code: Literal["child_meal"]
    at: datetime


class RefundIn(BaseModel):
    amount_paise: int | None = Field(
        default=None,
        le=10_000_000,
        description="At least ₹1 (100); null sends back everything refundable",
        examples=[41000],
    )


class RefundPartOut(BaseModel):
    topup_id: uuid.UUID
    razorpay_payment_id: str = Field(examples=["pay_EKwxwAgItmmXdp"])
    amount_paise: int = Field(examples=[20000])
    status: Literal["pending", "sent", "processed", "failed"]
    razorpay_refund_id: str | None = Field(examples=["rfnd_FP8DDKxqJif6ca"])


class RefundOut(BaseModel):
    id: uuid.UUID
    amount_paise: int = Field(examples=[41000])
    reason: Literal["customer", "account_deleted"]
    status: Literal["pending", "processing", "processed", "partly_failed", "failed"]
    parts: list[RefundPartOut] = Field(description="One per top-up taking money back, newest first")
    created_at: datetime
    settled_at: datetime | None


ReconKind = Literal[
    "captured_not_credited",
    "credited_not_captured",
    "amount_mismatch",
    "unknown_payment",
    "refund_status_mismatch",
    "refund_unknown",
    "refund_stuck",
]
ReconStatus = Literal["open", "healed", "resolved"]


class ReconResolution(BaseModel):
    by: uuid.UUID = Field(description="The admin who resolved it")
    note: str
    at: datetime


class ReconItem(BaseModel):
    id: uuid.UUID
    kind: ReconKind
    status: ReconStatus = Field(description="healed: the job repaired a lost webhook itself")
    razorpay_ref: str = Field(examples=["pay_EKwxwAgItmmXdp"])
    topup_id: uuid.UUID | None
    refund_part_id: uuid.UUID | None
    user_id: uuid.UUID | None
    expected_paise: int | None = Field(description="What our records say")
    actual_paise: int | None = Field(description="What Razorpay says")
    first_seen_at: datetime
    last_seen_at: datetime
    resolution: ReconResolution | None


class ReconRun(BaseModel):
    scheduled_for: datetime
    status: Literal["ok", "skipped", "failed"] = Field(
        description="skipped: no Razorpay keys; failed: Razorpay was unavailable, nothing changed"
    )
    checked: int
    healed: int
    opened: int


class ReconPage(BaseModel):
    items: list[ReconItem]
    next_cursor: str | None
    last_run: ReconRun | None


class ResolveIn(BaseModel):
    note: str = Field(
        min_length=1,
        max_length=500,
        description="The decision; a money correction is a separate, manual adjustment entry",
    )
