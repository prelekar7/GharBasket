"""Request and response models of M5 Billing & Ledger.

The wallet shapes are the S2 contract (docs/modules/M5-billing.md §3.1): changing them needs a PR
reviewed by both app owners.
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
