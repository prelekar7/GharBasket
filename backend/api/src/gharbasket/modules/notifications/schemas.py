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


# --- admin: templates and the delivery log (§3.2) ----------------------------------------------


class KindOut(BaseModel):
    kind: str = Field(examples=["orders.locked"])
    event_type: str
    category: str
    placeholders: list[str] = Field(description="What its templates may use", examples=[["total"]])


class TemplateOut(BaseModel):
    id: uuid.UUID
    kind: str = Field(examples=["orders.locked"])
    event_type: str
    channel: str = Field(description="app (inbox and push) or whatsapp")
    locale: str = Field(examples=["en-IN"])
    version: int
    title: str
    body: str
    wa_template_name: str | None = Field(description="Meta's approved template (whatsapp)")
    active: bool
    created_by: uuid.UUID | None
    created_at: datetime


class TemplatesOut(BaseModel):
    kinds: list[KindOut]
    items: list[TemplateOut]


class SavedTemplatesOut(BaseModel):
    items: list[TemplateOut] = Field(description="The new active version in each locale")


class TemplateText(BaseModel):
    locale: str = Field(max_length=5, examples=["en-IN"])
    title: str = Field(max_length=120, description="Empty for whatsapp")
    body: str = Field(min_length=1, max_length=500, examples=["{total} was paid from your wallet."])


class TemplateIn(BaseModel):
    # Plain strings so an unknown kind or channel gets `notifications.template_invalid`.
    kind: str = Field(max_length=64, examples=["orders.locked"])
    channel: str = Field(max_length=8, examples=["app"])
    wa_template_name: str | None = Field(default=None, max_length=100)
    texts: list[TemplateText] = Field(max_length=6, description="en-IN, hi-IN and mr-IN, once each")


class PreviewText(BaseModel):
    locale: str
    title: str
    body: str
    params: list[str] = Field(description="The body's values in order (WhatsApp's {{1}}, {{2}} …)")


class PreviewOut(BaseModel):
    items: list[PreviewText]


class DeliveryOut(BaseModel):
    channel: str = Field(examples=["push"])
    status: str = Field(description="queued, held, sent, delivered, read, failed or skipped")
    attempts: int
    last_error: str | None
    provider_ref: str | None
    next_attempt_at: datetime | None
    sent_at: datetime | None


class LogEntry(NotificationOut):
    event_id: uuid.UUID
    kind: str
    deliveries: list[DeliveryOut]


class DeliveryLogOut(BaseModel):
    items: list[LogEntry]
    next_cursor: str | None


class WhatsAppAck(BaseModel):
    outcomes: list[str] = Field(
        description="Per status update: applied, duplicate, unchanged or unknown_delivery"
    )
