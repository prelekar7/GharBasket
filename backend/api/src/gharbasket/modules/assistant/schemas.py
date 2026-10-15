"""Request and response models of M13 Assistant (Ghar Saathi), the S3 contract (spec §3)."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProposalAction = Literal["edit_box_line", "skip_week"]
ProposalStatus = Literal["pending", "confirmed", "rejected", "expired"]


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID | None = Field(default=None, description="Omit to start a session")
    text: str = Field(min_length=1, max_length=2000)


class ProposalOut(BaseModel):
    id: uuid.UUID
    action: ProposalAction
    payload: dict[str, Any]
    summary: str
    status: ProposalStatus
    expires_at: datetime
    result: dict[str, Any] | None = None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    tool_calls: list[str]
    created_at: datetime


class TurnOut(BaseModel):
    session_id: uuid.UUID
    reply: MessageOut
    proposals: list[ProposalOut]
    source: Literal["ml", "fallback"] = Field(
        description="fallback: the assistant was unreachable and a fixed reply was given"
    )
    tokens_today: int
    tokens_cap: int


class SessionOut(BaseModel):
    id: uuid.UUID
    locale: str
    model: str | None
    created_at: datetime
    messages: list[MessageOut]
    proposals: list[ProposalOut]


class ConfirmOut(BaseModel):
    proposal: ProposalOut
