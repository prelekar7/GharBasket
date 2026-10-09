"""Request and response models of M8 Partner Network (spec §3.3, frozen for S6).

No shape here ever carries a KYC number, an address or a phone, except `PartnerStaff`, which only
hub staff of the partner's hub and admins read.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PartnerKind = Literal["kitchen", "captain", "rider", "packer"]
PartnerStatus = Literal["active", "paused", "suspended", "left"]
SuspendReason = Literal["rating", "hygiene", "compliance", "conduct", "other"]


class RatingOut(BaseModel):
    average: float = Field(examples=[4.6])
    count: int = Field(examples=[23])


class SuspensionOut(BaseModel):
    at: datetime
    reason_code: Literal["rating", "hygiene", "compliance", "conduct", "other", "left"]
    note: str | None = Field(description="Staff-only: null in the partner's own view")


class PartnerOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    kind: PartnerKind
    hub_id: uuid.UUID
    display_name: str = Field(examples=["Meena tai"])
    photo_url: str | None = Field(description="Always null for now (M8 §11)")
    status: PartnerStatus
    rating: RatingOut | None = Field(description="Kitchens with 5 ratings or more; else null")
    verified_at: datetime
    suspension: SuspensionOut | None
    version: int


class PartnersOut(BaseModel):
    items: list[PartnerOut]


class SocietyRef(BaseModel):
    id: uuid.UUID
    name: str


class PartnerStaffOut(PartnerOut):
    """A partner as hub staff of her hub (and admins) see her."""

    name: str | None = Field(description="Her M1 name")
    phone_e164: str | None = Field(examples=["+919812345678"])
    application_id: uuid.UUID | None = Field(description="null for seeded partners")
    requested_society: SocietyRef | None = Field(
        description="Captains: the society on her application; null without one"
    )


def _note(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


class SuspendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: SuspendReason
    note: str = Field(min_length=1, max_length=300, description="Staff-only")

    @field_validator("note")
    @classmethod
    def _tidy(cls, value: str) -> str:
        tidy = _note(value)
        if tidy is None:
            raise ValueError("note must not be blank")
        return tidy


class ReinstateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=300, description="Staff-only")

    @field_validator("note")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return _note(value)


# --- applications (§3.1) and training (§3.2) ----------------------------------------------------

ApplicationStatus = Literal[
    "applied", "kyc_pending", "training", "verified", "rejected", "withdrawn"
]
KIND_DESCRIPTION = "kitchen, captain, rider or packer (else 422 partners.kind_invalid)"


class ApplicationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=16, description=KIND_DESCRIPTION, examples=["captain"])
    scope_id: uuid.UUID | None = Field(
        default=None,
        description="Optional: a captain's own society, anyone else's household hub",
    )
    display_name: str | None = Field(
        default=None,
        max_length=100,
        description="What customers will see, 2 to 40 characters; default your M1 name",
        examples=["Meena tai"],
    )


class HubApplicationIn(BaseModel):
    """A rider or packer application a hub manager creates for a person without a household."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(max_length=16, description="rider or packer", examples=["rider"])
    phone_e164: str = Field(
        pattern=r"^\+[1-9][0-9]{7,14}$",
        description="A signed-up user's phone",
        examples=["+919812345678"],
    )
    display_name: str = Field(max_length=100, description="2 to 40 characters", examples=["Ravi"])


class DocumentRequirement(BaseModel):
    doc_type: str = Field(examples=["pan"])
    status: Literal["missing", "submitted", "accepted", "rejected"]
    document_id: uuid.UUID | None
    reason: str | None = Field(description="Why the hub asked for it again")


class TrainingRequirement(BaseModel):
    code: str = Field(examples=["hygiene"])
    status: Literal["todo", "passed", "failed"]


class Requirements(BaseModel):
    documents: list[DocumentRequirement] = Field(description="The kind's required types only")
    trainings: list[TrainingRequirement]


class ApplicationOut(BaseModel):
    id: uuid.UUID
    kind: PartnerKind
    status: ApplicationStatus
    scope_type: Literal["society", "hub"] = Field(
        description="society: the captain's own society; hub: everyone else"
    )
    scope_id: uuid.UUID
    hub_id: uuid.UUID
    display_name: str = Field(examples=["Meena tai"])
    requirements: Requirements
    next_step: Literal["documents", "review", "training"] | None = Field(
        description="null once verified, rejected or withdrawn"
    )
    reason: str | None = Field(description="Why it was rejected or sent back")
    partner_id: uuid.UUID | None = Field(description="Set at verification")
    submitted_at: datetime
    decided_at: datetime | None
    verified_at: datetime | None
    version: int


class ApplicantOut(BaseModel):
    user_id: uuid.UUID
    name: str | None
    phone_e164: str | None


class ApplicationSocietyOut(BaseModel):
    id: uuid.UUID
    name: str | None
    has_active_captain: bool = Field(
        description="Whether the society already has an active captain (M9)"
    )


class ApplicationReviewOut(ApplicationOut):
    """An application as hub staff of its hub (and admins) see it."""

    applicant: ApplicantOut
    society: ApplicationSocietyOut | None = Field(description="Captain applications")


class TrainingQuestion(BaseModel):
    id: str = Field(examples=["q1"])
    text: str
    options: list[str] = Field(description="Answer with the option's index, from 0")


class TrainingModuleOut(BaseModel):
    code: str = Field(examples=["hygiene"])
    version: int
    title: str = Field(examples=["Kitchen hygiene"])
    summary: str
    minutes: int
    video_url: str | None = Field(description="An unlisted video; null for a read-only module")
    for_kinds: list[PartnerKind]
    questions: list[TrainingQuestion] = Field(description="The right answers are never sent")
    pass_score: float = Field(examples=[0.8])
    status: Literal["todo", "passed", "failed"]
    best_score: float | None
    passed_at: datetime | None


class TrainingModulesOut(BaseModel):
    items: list[TrainingModuleOut]


class TrainingAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(max_length=16, examples=["q1"])
    option: int = Field(ge=0, le=20, description="The option's index, from 0")


class TrainingCompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1, le=1000, description="The module version answered")
    answers: list[TrainingAnswer] = Field(max_length=20, description="One per question")


class ApplicationStepOut(BaseModel):
    id: uuid.UUID
    status: ApplicationStatus
    next_step: Literal["documents", "review", "training"] | None


class TrainingResultOut(BaseModel):
    code: str
    score: float = Field(examples=[0.9])
    correct: int
    total: int
    passed: bool = Field(description="This attempt")
    status: Literal["passed", "failed"] = Field(description="The module: a pass is kept for good")
    application: ApplicationStepOut | None = Field(description="Your open application, if any")
