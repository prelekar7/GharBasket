"""Request and response models of M1 Identity & Household.

The exchange request/response and UserOut are the frozen walking-skeleton contract
(docs/modules/M1-identity.md §3.1): changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import datetime
from typing import Literal, Self

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Locale = Literal["en-IN", "hi-IN", "mr-IN"]


class DeviceIn(BaseModel):
    platform: Literal["android", "web"]
    push_token: str | None = Field(default=None, max_length=4096)


class ExchangeIn(BaseModel):
    """Exactly one of `firebase_id_token` (always) or `dev_phone` (only with APP_ENV=dev)."""

    firebase_id_token: str | None = Field(default=None, max_length=8192)
    dev_phone: str | None = Field(default=None, examples=["+919800000001"])
    dev_name: str | None = Field(default=None, max_length=80, examples=["Priya"])
    device: DeviceIn

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
    ]
    scope_id: uuid.UUID | None = None

    @field_validator("phone_e164")
    @classmethod
    def _e164(cls, value: str) -> str:
        return _normalise_phone(value)


class RoleGrantOut(RoleOut):
    user_id: uuid.UUID
