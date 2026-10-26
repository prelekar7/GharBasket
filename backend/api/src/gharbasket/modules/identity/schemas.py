"""Request and response models of M1 Identity & Household.

The exchange request/response and UserOut are the frozen walking-skeleton contract
(docs/modules/M1-identity.md §3.1), and the household shapes the S1 contract (§3.2): changing
them needs a PR reviewed by both app owners.
"""

import re
import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal, Self

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from gharbasket.platform.geo import LatLng

Locale = Literal["en-IN", "hi-IN", "mr-IN"]


class DeviceIn(BaseModel):
    platform: Literal["android", "web"]
    push_token: str | None = Field(default=None, max_length=4096)


class PushTokenIn(BaseModel):
    push_token: str | None = Field(
        min_length=1,
        max_length=4096,
        description="The FCM registration token of this device; null when notifications are off",
    )


class ExchangeIn(BaseModel):
    """Exactly one of `firebase_id_token` (always) or `dev_phone` (only with APP_ENV=dev)."""

    firebase_id_token: str | None = Field(default=None, max_length=8192)
    dev_phone: str | None = Field(default=None, examples=["+919800000001"])
    dev_name: str | None = Field(default=None, max_length=80, examples=["Priya"])
    device: DeviceIn
    keep_account: bool = Field(
        default=False,
        description=(
            'For an account whose deletion is scheduled: true (after the app asked "Keep my '
            'account?") signs in and cancels the deletion; false gets 409 '
            "identity.deletion_pending (spec §3.5)"
        ),
    )

    @field_validator("dev_phone")
    @classmethod
    def _e164(cls, value: str | None) -> str | None:
        return None if value is None else _normalise_phone(value)

    @model_validator(mode="after")
    def _one_way_in(self) -> Self:
        if (self.firebase_id_token is None) == (self.dev_phone is None):
            raise ValueError("send exactly one of firebase_id_token or dev_phone")
        return self


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    phone_e164: str
    name: str | None
    locale: Locale
    senior_mode: bool


class TokensOut(BaseModel):
    """Apps get both tokens. Browsers (`X-GB-Client: web`) get null here: the tokens are set as
    httpOnly cookies that JavaScript cannot read."""

    access_token: str | None
    access_expires_in: int = Field(examples=[900])
    refresh_token: str | None


class ExchangeOut(TokensOut):
    is_new_user: bool
    user: UserOut
    deletion_cancelled: bool = Field(
        default=False, description="True when this sign-in cancelled a scheduled deletion"
    )


class WalletRefundOut(BaseModel):
    refund_id: uuid.UUID
    amount_paise: int = Field(examples=[41000])


class DeletionOut(BaseModel):
    """DELETE /v1/me (spec §3.5): the account is deleted on `purge_on` unless the customer signs
    in again before then."""

    status: Literal["scheduled"]
    requested_at: datetime
    purge_on: date = Field(description="IST date, 30 days after the request")
    wallet_refund: WalletRefundOut | None = Field(
        description="The wallet balance on its way back to the original payment source"
    )


class RefreshIn(BaseModel):
    """Apps send the refresh token; browsers send `{}` and the `gb_rt` cookie carries it."""

    refresh_token: str | None = Field(default=None, min_length=1, max_length=256)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    scope_type: Literal["society", "hub", "ngo"] | None
    scope_id: uuid.UUID | None
    status: Literal["applied", "kyc_pending", "verified", "suspended"]


class MeOut(UserOut):
    roles: list[RoleOut]


def _normalise_phone(value: str) -> str:
    try:
        number = phonenumbers.parse(value, "IN")
    except phonenumbers.NumberParseException as exc:
        raise ValueError("not a phone number") from exc
    if not phonenumbers.is_valid_number(number):
        raise ValueError("not a valid phone number")
    return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


normalise_phone = _normalise_phone


class MeUpdate(BaseModel):
    """Only the fields sent are changed. `name: null` clears the name."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    locale: Locale | None = None
    senior_mode: bool | None = None

    @field_validator("name")
    @classmethod
    def _tidy_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        tidy = " ".join(value.split())
        if not tidy:
            raise ValueError("name cannot be blank")
        return tidy

    @model_validator(mode="after")
    def _not_null_settings(self) -> Self:
        for field in ("locale", "senior_mode"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class DeviceOut(BaseModel):
    """A signed-in device or browser (one session)."""

    id: uuid.UUID
    platform: Literal["android", "web"]
    signed_in_at: datetime
    last_seen_at: datetime
    current: bool


class RoleGrantIn(BaseModel):
    """Grant a role to an existing user (they must have signed in once)."""

    model_config = ConfigDict(extra="forbid")

    phone_e164: str = Field(examples=["+919800000001"])
    role: Literal[
        "captain",
        "kitchen_partner",
        "rider",
        "packer",
        "hub_manager",
        "franchise_owner",
        "ngo_coordinator",
        "admin",
        "catalog_editor",
    ]
    scope_id: uuid.UUID | None = None

    @field_validator("phone_e164")
    @classmethod
    def _e164(cls, value: str) -> str:
        return _normalise_phone(value)


class RoleGrantOut(RoleOut):
    user_id: uuid.UUID


# --- household (spec §3.2, frozen for S1) ------------------------------------------------------

Diet = Literal["veg", "egg", "nonveg"]
Cuisine = Literal["maharashtrian", "gujarati", "north_indian", "south_indian", "bengali"]
AgeBand = Literal["under_5", "5_17", "18_59", "60_plus"]
Fast = Literal["shravan_mondays", "ekadashi", "navratri", "tuesdays"]
HealthTag = Literal["diabetes", "high_bp", "pcos", "low_oil", "high_protein", "weight_loss"]
ConsentPurpose = Literal[
    "service_delivery",
    "personalisation",
    "health_personalisation",
    "marketing",
    "research_anonymised",
    "whatsapp",
]
ShortagePreference = Literal["substitute", "refund"]
CookMinutes = Annotated[int, Field(ge=10, le=240)]
Weekday = Annotated[int, Field(ge=0, le=6)]
FLAT = re.compile(r"^[A-Z0-9 /-]{1,20}$")


def _unique[T](values: list[T]) -> list[T]:
    if len(set(values)) != len(values):
        raise ValueError("values must be unique")
    return values


class FastingRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jain: bool = False
    fasts: list[Fast] = Field(default_factory=list, max_length=4)
    no_onion_garlic_on_fast_days: bool = False

    _unique_fasts = field_validator("fasts")(_unique)


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_band: AgeBand
    sex: Literal["f", "m", "other"] | None = None
    fasting_rules: FastingRules = Field(default_factory=FastingRules)
    health_tags: list[HealthTag] = Field(
        default_factory=list,
        max_length=6,
        description="Only while health_personalisation is granted (403 otherwise)",
    )

    _unique_tags = field_validator("health_tags")(_unique)


class MemberOut(MemberIn):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID


class AddressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tower_id: str = Field(min_length=1, max_length=20, examples=["a"])
    flat: str = Field(examples=["A-1203"], description="Trimmed and upper-cased")
    location: LatLng | None = Field(description="null when no pin was placed")

    @field_validator("flat")
    @classmethod
    def tidy_flat(cls, value: str) -> str:
        flat = " ".join(value.split()).upper()
        if not FLAT.fullmatch(flat):
            raise ValueError("1 to 20 letters, digits, spaces, '/' or '-'")
        return flat


class AddressOut(BaseModel):
    id: uuid.UUID
    household_id: uuid.UUID
    society_id: uuid.UUID
    tower_id: str
    flat: str
    location: LatLng | None


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: ConsentPurpose
    version: str = Field(max_length=10, examples=["v1"])
    # Consent must be explicit (DPDP): only JSON true or false, never 0/1 or "yes".
    granted: StrictBool


class ConsentOut(BaseModel):
    purpose: ConsentPurpose
    version: str
    granted: bool
    granted_at: datetime | None
    revoked_at: datetime | None


class HouseholdSettings(BaseModel):
    """The editable part of a household (also what PATCH accepts)."""

    model_config = ConfigDict(extra="forbid")

    diet: Diet
    cuisine_tags: list[Cuisine] = Field(default_factory=list, max_length=5)
    weekday_cook_minutes: CookMinutes = Field(examples=[30])
    weekend_cook_minutes: CookMinutes = Field(examples=[60])
    no_cook_days: list[Weekday] = Field(
        default_factory=list, max_length=7, description="0 = Monday … 6 = Sunday"
    )
    budget_paise: int | None = Field(
        default=None, ge=50_000, examples=[300_000], description="Monthly Budget Lock; null = off"
    )
    shortage_preference: ShortagePreference = Field(
        default="substitute",
        description="A short item: a similar one at the same price or cheaper, or never "
        "substitute and refund (§3.4)",
    )

    _unique_cuisines = field_validator("cuisine_tags")(_unique)
    _unique_days = field_validator("no_cook_days")(_unique)


class HouseholdIn(HouseholdSettings):
    """Onboarding in one call: household, members, address and consents together."""

    society_id: uuid.UUID
    address: AddressIn
    members: list[MemberIn] = Field(min_length=1, max_length=12)
    consents: list[ConsentIn] = Field(max_length=5)

    @field_validator("consents")
    @classmethod
    def _one_per_purpose(cls, value: list[ConsentIn]) -> list[ConsentIn]:
        _unique([consent.purpose for consent in value])
        return value


class HouseholdUpdate(BaseModel):
    """Only the fields sent change; `budget_paise: null` turns the Budget Lock off."""

    model_config = ConfigDict(extra="forbid")

    diet: Diet | None = None
    cuisine_tags: list[Cuisine] | None = Field(default=None, max_length=5)
    weekday_cook_minutes: CookMinutes | None = None
    weekend_cook_minutes: CookMinutes | None = None
    no_cook_days: list[Weekday] | None = Field(default=None, max_length=7)
    budget_paise: int | None = Field(default=None, ge=50_000)
    autopilot_enabled: bool | None = None
    auto_approve: bool | None = None
    shortage_preference: ShortagePreference | None = None

    @field_validator("cuisine_tags", "no_cook_days")
    @classmethod
    def _unique_values(cls, value: list[Any] | None) -> list[Any] | None:
        return None if value is None else _unique(value)

    @model_validator(mode="after")
    def _only_budget_may_be_null(self) -> Self:
        for field in self.model_fields_set - {"budget_paise"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class HouseholdOut(BaseModel):
    id: uuid.UUID
    version: int
    society_id: uuid.UUID
    hub_id: uuid.UUID | None
    size: int
    diet: Diet
    cuisine_tags: list[Cuisine]
    weekday_cook_minutes: int
    weekend_cook_minutes: int
    no_cook_days: list[int]
    budget_paise: int | None
    autopilot_enabled: bool
    auto_approve: bool
    shortage_preference: ShortagePreference
    members: list[MemberOut]
    address: AddressOut
    created_at: datetime


class MemberUpdate(BaseModel):
    """Only the fields sent change; `fasting_rules` is replaced whole. `sex: null` clears it."""

    model_config = ConfigDict(extra="forbid")

    age_band: AgeBand | None = None
    sex: Literal["f", "m", "other"] | None = None
    fasting_rules: FastingRules | None = None
    health_tags: list[HealthTag] | None = Field(default=None, max_length=6)

    @field_validator("health_tags")
    @classmethod
    def _unique_tags(cls, value: list[HealthTag] | None) -> list[HealthTag] | None:
        return None if value is None else _unique(value)

    @model_validator(mode="after")
    def _only_sex_may_be_null(self) -> Self:
        for field in self.model_fields_set - {"sex"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class AddressUpdate(BaseModel):
    """Only the fields sent change; `location: null` removes the pin.

    Moving to another society moves the household with it, and the tower (and pin, if any) must
    then fit the new society.
    """

    model_config = ConfigDict(extra="forbid")

    society_id: uuid.UUID | None = None
    tower_id: str | None = Field(default=None, min_length=1, max_length=20)
    flat: str | None = Field(default=None, examples=["A-1203"])
    location: LatLng | None = None

    @field_validator("flat")
    @classmethod
    def _tidy_flat(cls, value: str | None) -> str | None:
        return None if value is None else AddressIn.tidy_flat(value)

    @model_validator(mode="after")
    def _only_location_may_be_null(self) -> Self:
        for field in self.model_fields_set - {"location"}:
            if getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


# --- data export (§3.3) -------------------------------------------------------------------------


class DataExportDownload(BaseModel):
    url: str = Field(description="A signed GET link, valid 5 minutes; GET again for a fresh one")
    expires_at: datetime
    size_bytes: int = Field(examples=[48211])


class DataExportOut(BaseModel):
    """A copy of your data: one JSON file, built within a day and kept 7 days."""

    id: uuid.UUID
    status: Literal["queued", "building", "ready", "failed", "expired"]
    requested_at: datetime
    ready_at: datetime | None
    expires_at: datetime | None = Field(description="When the file is deleted (7 days after ready)")
    ready_within_hours: int = Field(examples=[24])
    download: DataExportDownload | None = Field(description="Only while ready")


# --- staff lookup, revoke and the hub's staff (spec §3.6) ----------------------------------------

ScopeKind = Literal["society", "hub", "ngo"]
RoleStatus = Literal["applied", "kyc_pending", "verified", "suspended"]
StaffRole = Literal["admin", "hub_manager", "franchise_owner", "ngo_coordinator", "catalog_editor"]
AnyRole = Literal[
    "customer",
    "captain",
    "kitchen_partner",
    "rider",
    "packer",
    "hub_manager",
    "franchise_owner",
    "ngo_coordinator",
    "admin",
    "catalog_editor",
]


class StaffGrant(BaseModel):
    """One `user_roles` row; DELETE /v1/admin/user-roles/{id} takes its id."""

    id: uuid.UUID
    role: AnyRole
    scope_type: ScopeKind | None
    scope_id: uuid.UUID | None
    scope_label: str | None = Field(examples=["Kharghar hub", "Sunrise Towers"])
    status: RoleStatus
    verified_at: datetime | None


class AdminUser(BaseModel):
    id: uuid.UUID
    name: str | None
    phone_e164: str
    locale: Locale
    status: Literal["active", "blocked", "deleting"]
    last_seen_at: datetime | None = Field(description="The latest session's")
    roles: list[StaffGrant] = Field(description="Every role, the customer one included")


class PendingGrant(BaseModel):
    """A staff role given to a number that has not signed in yet; it waits up to 30 days."""

    id: uuid.UUID
    phone_masked: str = Field(examples=["******1011"])
    role: StaffRole
    scope_type: ScopeKind | None
    scope_id: uuid.UUID | None
    scope_label: str | None
    status: Literal["pending"] = "pending"
    granted_at: datetime
    expires_at: datetime


class HubStaffSociety(BaseModel):
    id: uuid.UUID
    name: str


class HubStaff(BaseModel):
    grant_id: uuid.UUID = Field(description="The user_roles row, or the pending grant")
    user_id: uuid.UUID | None = Field(description="Null for an invited number")
    name: str | None
    phone_masked: str = Field(description="The last 4 digits only", examples=["******1011"])
    role: AnyRole
    works_as: Literal["staff", "partner"] = Field(
        description="partner: kitchen_partner, captain, rider, packer"
    )
    society: HubStaffSociety | None = Field(description="Captains: the society they serve")
    status: Literal["applied", "kyc_pending", "verified", "suspended", "invited"]
    since: datetime | None = Field(
        description="verified_at, or the pending grant's granted_at; null before verification"
    )
