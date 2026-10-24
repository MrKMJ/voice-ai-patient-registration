"""Request/response models. All field rules live in app/validators.py."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, Generic, TypeVar

from pydantic import BaseModel, BeforeValidator, ConfigDict, field_serializer

from app.validators import optional_validator, required_validator

T = TypeVar("T")

# Required on create; when present on update, null/blank is rejected.
FirstName = Annotated[str, BeforeValidator(required_validator("first_name"))]
LastName = Annotated[str, BeforeValidator(required_validator("last_name"))]
DateOfBirth = Annotated[date, BeforeValidator(required_validator("date_of_birth"))]
Sex = Annotated[str, BeforeValidator(required_validator("sex"))]
Phone = Annotated[str, BeforeValidator(required_validator("phone_number"))]
Address1 = Annotated[str, BeforeValidator(required_validator("address_line_1"))]
City = Annotated[str, BeforeValidator(required_validator("city"))]
State = Annotated[str, BeforeValidator(required_validator("state"))]
Zip = Annotated[str, BeforeValidator(required_validator("zip_code"))]

# Optional: blank/null means "not provided" (and clears the value on update).
Email = Annotated[str | None, BeforeValidator(optional_validator("email"))]
Address2 = Annotated[str | None, BeforeValidator(optional_validator("address_line_2"))]
InsProvider = Annotated[str | None, BeforeValidator(optional_validator("insurance_provider"))]
InsMemberId = Annotated[str | None, BeforeValidator(optional_validator("insurance_member_id"))]
Language = Annotated[
    str, BeforeValidator(optional_validator("preferred_language", default="English"))
]
EmergencyName = Annotated[
    str | None, BeforeValidator(optional_validator("emergency_contact_name"))
]
EmergencyPhone = Annotated[
    str | None, BeforeValidator(optional_validator("emergency_contact_phone"))
]


class PatientCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: FirstName
    last_name: LastName
    date_of_birth: DateOfBirth
    sex: Sex
    phone_number: Phone
    email: Email = None
    address_line_1: Address1
    address_line_2: Address2 = None
    city: City
    state: State
    zip_code: Zip
    insurance_provider: InsProvider = None
    insurance_member_id: InsMemberId = None
    preferred_language: Language = "English"
    emergency_contact_name: EmergencyName = None
    emergency_contact_phone: EmergencyPhone = None


class PatientUpdate(BaseModel):
    """Partial update: only the fields present in the body are changed."""

    model_config = ConfigDict(extra="forbid")

    first_name: FirstName = None  # type: ignore[assignment]
    last_name: LastName = None  # type: ignore[assignment]
    date_of_birth: DateOfBirth = None  # type: ignore[assignment]
    sex: Sex = None  # type: ignore[assignment]
    phone_number: Phone = None  # type: ignore[assignment]
    email: Email = None
    address_line_1: Address1 = None  # type: ignore[assignment]
    address_line_2: Address2 = None
    city: City = None  # type: ignore[assignment]
    state: State = None  # type: ignore[assignment]
    zip_code: Zip = None  # type: ignore[assignment]
    insurance_provider: InsProvider = None
    insurance_member_id: InsMemberId = None
    preferred_language: Language = None  # type: ignore[assignment]
    emergency_contact_name: EmergencyName = None
    emergency_contact_phone: EmergencyPhone = None


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    first_name: str
    last_name: str
    date_of_birth: date
    sex: str
    phone_number: str
    email: str | None
    address_line_1: str
    address_line_2: str | None
    city: str
    state: str
    zip_code: str
    insurance_provider: str | None
    insurance_member_id: str | None
    preferred_language: str
    emergency_contact_name: str | None
    emergency_contact_phone: str | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None

    @field_serializer("date_of_birth")
    def _dob(self, value: date) -> str:
        return value.strftime("%m/%d/%Y")

    @field_serializer("created_at", "updated_at", "deleted_at")
    def _ts(self, value: datetime | None) -> str | None:
        return _utc_iso(value)


class CallLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    call_id: str
    patient_id: uuid.UUID | None
    caller_number: str | None
    ended_reason: str | None
    summary: str | None
    transcript: str | None
    created_at: datetime

    @field_serializer("created_at")
    def _ts(self, value: datetime) -> str | None:
        return _utc_iso(value)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    scheduled_for: datetime
    provider: str
    reason: str | None
    status: str

    @field_serializer("scheduled_for")
    def _when(self, value: datetime) -> str:
        return value.isoformat()


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = []


class Envelope(BaseModel, Generic[T]):
    """Every response: {"data": ..., "error": null} or {"data": null, "error": {...}}."""

    data: T | None = None
    error: ErrorBody | None = None
    meta: dict[str, Any] | None = None
