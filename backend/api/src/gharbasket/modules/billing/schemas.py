"""Request and response models of M5 Billing & Ledger.

The wallet shapes are the S2 contract (docs/modules/M5-billing.md §3.1) and the statement is §3.2:
changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

EntryKind = Literal["topup", "topup_bonus", "box_debit", "refund", "adjustment"]


class TopUpRef(BaseModel):
    type: Literal["topup"]
    id: uuid.UUID


class OrderRef(BaseModel):
    type: Literal["order"]
    id: uuid.UUID
    delivery_date: date | None


class Entry(BaseModel):
    id: uuid.UUID
    kind: EntryKind = Field(description="Show a generic label for a kind the app doesn't know")
    amount_paise: int = Field(description="Into the wallet +, out of it -", examples=[-69000])
    ref: Annotated[TopUpRef | OrderRef, Field(discriminator="type")] | None
    at: datetime


class WalletOut(BaseModel):
    balance_paise: int = Field(examples=[131000])
    cap_paise: int = Field(examples=[1000000])
    topup_room_paise: int = Field(
        description="What can still be added now: the cap minus the balance and unpaid top-ups, "
        "limited by this month's allowance",
        examples=[869000],
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
