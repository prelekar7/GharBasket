"""Request and response models of M8 Partner Network (spec §3.1 to §3.3, frozen for S6).

No shape here ever carries a KYC number in clear (only masked), an address or a phone, except
`PartnerStaff` and `ApplicationReview`, which only hub staff of the hub and admins read.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gharbasket.platform.geo import LatLng

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


class KycDocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    application_id: uuid.UUID
    doc_type: str = Field(
        max_length=24,
        description="id_proof, pan, bank, fssai, driving_licence or kitchen_photos; only the "
        "kind's required types",
        examples=["pan"],
    )
    id_kind: str | None = Field(
        default=None,
        max_length=24,
        description="id_proof only: voter_id, passport or driving_licence; never Aadhaar",
    )
    number: str | None = Field(
        default=None,
        max_length=40,
        description="Every type but kitchen_photos; bank: the account number. Upper-cased, "
        "spaces and hyphens removed",
        examples=["ABCDE1234F"],
    )
    ifsc: str | None = Field(default=None, max_length=20, examples=["SBIN0001234"])
    account_holder: str | None = Field(default=None, max_length=100, description="bank only")
    valid_until: date | None = Field(
        default=None, description="fssai and driving_licence; must be in the future"
    )
    vehicle_no: str | None = Field(
        default=None, max_length=20, description="driving_licence only", examples=["MH46AB1234"]
    )
    file_keys: list[Annotated[str, Field(max_length=255)]] = Field(
        default_factory=list,
        max_length=4,
        description="Keys from POST /v1/files/presign (kyc_document): kitchen_photos 2 to 4, "
        "others 0 to 2",
    )


class KycCheckOut(BaseModel):
    status: Literal["passed", "failed"]
    code: Literal["format_ok", "penny_drop_ok", "penny_drop_failed", "duplicate"]
    at: datetime


class KycReviewOut(BaseModel):
    status: Literal["pending", "accepted", "rejected"]
    reason: str | None
    at: datetime | None


class KycDocumentOut(BaseModel):
    id: uuid.UUID
    application_id: uuid.UUID
    doc_type: str
    id_kind: str | None
    number_masked: str | None = Field(
        description="Only the last 4 characters", examples=["XXXXXX234F"]
    )
    fssai_reg_no: str | None = Field(description="fssai only, in full (public by law)")
    details: dict[str, str] = Field(description="ifsc, account_holder, valid_until, vehicle_no")
    file_count: int
    check: KycCheckOut
    review: KycReviewOut
    created_at: datetime


class ApplicationDetailOut(ApplicationOut):
    """The applicant's own application with its current documents."""

    documents: list[KycDocumentOut]


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["approve", "resubmit", "reject"]
    reason: str | None = Field(
        default=None,
        max_length=300,
        description="Required for resubmit and reject; shown to the applicant",
    )
    document_ids: list[uuid.UUID] = Field(
        default_factory=list, max_length=10, description="resubmit: the documents to redo"
    )
    hygiene_grade: Literal["A", "B", "C"] | None = Field(
        default=None, description="Approving a kitchen: required (from its photos)"
    )

    @field_validator("reason")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return _note(value)


class FileLinkOut(BaseModel):
    url: str
    content_type: str
    expires_at: datetime


class FileLinksOut(BaseModel):
    files: list[FileLinkOut]


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
    documents: list[KycDocumentOut] = Field(description="The current documents")


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


# --- kitchens and capacity (§3.4) ---------------------------------------------------------------

Meal = Literal["lunch", "dinner"]
SKILLS_DESCRIPTION = (
    "From veg, jain, no_onion_garlic, diabetic, egg, nonveg (else 422 partners.skills_invalid)"
)


class WeekPortions(BaseModel):
    lunch: list[int] = Field(description="Monday … Sunday, 0 to 100 each", examples=[[10] * 7])
    dinner: list[int] = Field(description="Monday … Sunday, 0 to 100 each", examples=[[0] * 7])


class HygieneOut(BaseModel):
    grade: Literal["A", "B", "C"]
    inspected_on: date


class KitchenOut(BaseModel):
    """A kitchen as she and hub staff see it: `address` and `location` are private."""

    partner_id: uuid.UUID
    display_name: str = Field(examples=["Lata tai"])
    status: PartnerStatus
    area: str | None = Field(description="Public (the card)", examples=["Sector 20, Kharghar"])
    address: str | None = Field(description="Private: the kitchen, hub staff, the rider")
    location: LatLng | None = Field(description="Private; null until set (no offers before)")
    fssai_reg_no: str | None = Field(examples=["11521999000123"])
    fssai_valid_until: date | None
    skills: list[str] = Field(examples=[["veg", "jain"]])
    default_portions: WeekPortions
    hygiene: HygieneOut | None
    version: int


class KitchenPatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, min_length=2, max_length=40)
    area: str | None = Field(default=None, max_length=80)
    address: str | None = Field(default=None, max_length=300)
    location: LatLng | None = None
    skills: list[Annotated[str, Field(max_length=32)]] | None = Field(
        default=None, max_length=12, description=SKILLS_DESCRIPTION
    )
    default_portions: dict[str, list[int]] | None = Field(
        default=None,
        description="lunch and dinner, Monday … Sunday, 0 to 100 each "
        "(else 422 partners.capacity_invalid)",
    )

    @field_validator("area", "address")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return _note(value)


class InspectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grade: Literal["A", "B", "C"]
    note: str | None = Field(default=None, max_length=300, description="Staff-only; audit log")

    @field_validator("note")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return _note(value)


class CapacityOut(BaseModel):
    date: date
    meal: Meal
    max_portions: int = Field(examples=[12])
    source: Literal["default", "set"]
    assigned_portions: int = Field(description="In offered, accepted, preparing, ready offers")
    locked: bool = Field(description="From 21:00 IST the day before")


class CapacitiesOut(BaseModel):
    items: list[CapacityOut]


class CapacityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    meal: str = Field(max_length=16, description="lunch or dinner", examples=["lunch"])
    max_portions: int = Field(
        description="0 to 100 (else 422 partners.capacity_invalid); 0 = no tiffins", examples=[12]
    )


# --- tiffin assignments (§3.4) ------------------------------------------------------------------


class RunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date


class RunOut(BaseModel):
    run_id: uuid.UUID | None = Field(description="The first meal's run; null with nothing to do")
    run_ids: list[uuid.UUID] = Field(description="One run per meal that had orders")
    offers: int
    unassigned: int
