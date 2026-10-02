"""Request and response models of M1 Identity & Household.

The exchange request/response and UserOut are the frozen walking-skeleton contract
(docs/modules/M1-identity.md §3.1): changing them needs a PR reviewed by both app owners.
"""

import uuid
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
        if value is None:
            return None
        try:
            number = phonenumbers.parse(value, "IN")
        except phonenumbers.NumberParseException as exc:
            raise ValueError("not a phone number") from exc
        if not phonenumbers.is_valid_number(number):
            raise ValueError("not a valid phone number")
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)

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
    access_token: str
    access_expires_in: int = Field(examples=[900])
    refresh_token: str


class ExchangeOut(TokensOut):
    is_new_user: bool
    user: UserOut


class RefreshIn(BaseModel):
    refresh_token: str = Field(min_length=1, max_length=256)


class RoleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    scope_type: Literal["society", "hub", "ngo"] | None
    scope_id: uuid.UUID | None
    status: Literal["applied", "kyc_pending", "verified", "suspended"]


class MeOut(UserOut):
    roles: list[RoleOut]
