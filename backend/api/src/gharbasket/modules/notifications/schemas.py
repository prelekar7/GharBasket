"""Request and response models of M11 Notifications.

The inbox and preferences shapes are the contract in docs/modules/M11-notifications.md §3.1:
changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationOut(BaseModel):
    id: uuid.UUID
    event_type: str = Field(examples=["orders.at_society"])
    category: str = Field(
        description="weekly_box, delivery, money, kindness, offers or account",
        examples=["delivery"],
    )
    title: str = Field(examples=["Your box has reached your society"])
    body: str
    link: str | None = Field(description="An app route", examples=["/orders"])
    created_at: datetime
    read_at: datetime | None


class InboxOut(BaseModel):
    items: list[NotificationOut]
    unread: int = Field(description="Unread messages in the whole inbox, not only this page")
    next_cursor: str | None


class CategoryPreference(BaseModel):
    # A plain string so an unknown category gets `notifications.category_unknown`.
    category: str = Field(max_length=32, examples=["weekly_box"])
    push: bool
    whatsapp: bool


class PreferencesOut(BaseModel):
    categories: list[CategoryPreference] = Field(
        description="All five, in the order weekly_box, delivery, money, kindness, offers"
    )
    quiet_start: str = Field(description="IST, HH:MM", examples=["22:00"])
    quiet_end: str = Field(description="IST, HH:MM", examples=["07:00"])


class PreferencesIn(BaseModel):
    categories: list[CategoryPreference] = Field(
        max_length=10, description="Categories left out keep their value"
    )
    quiet_start: str | None = Field(
        default=None, max_length=5, description="IST, HH:MM in 15-minute steps"
    )
    quiet_end: str | None = Field(default=None, max_length=5)
