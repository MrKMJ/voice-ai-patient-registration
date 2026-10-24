"""Patient data layer. Shared by the REST routes and the voice agent tools.

Functions add/flush but never commit: the caller owns the transaction, which lets the
voice layer save a patient and link the call log in one commit.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import Patient, utcnow
from app.schemas import PatientCreate, PatientUpdate
from app.validators import validate_date_of_birth, validate_field


def parse_patient_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except (ValueError, AttributeError):
        raise ApiError(400, "bad_request", "patient_id must be a valid UUID.") from None


def create_patient(db: Session, payload: PatientCreate) -> Patient:
    patient = Patient(**payload.model_dump())
    db.add(patient)
    db.flush()
    return patient


def get_patient(db: Session, patient_id: uuid.UUID) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None or patient.deleted_at is not None:
        raise ApiError(404, "not_found", "Patient not found.")
    return patient


def list_patients(
    db: Session,
    *,
    last_name: str | None = None,
    date_of_birth: str | None = None,
    phone_number: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[Patient], int]:
    stmt = select(Patient).where(Patient.deleted_at.is_(None))
    try:
        if last_name:
            stmt = stmt.where(func.lower(Patient.last_name) == last_name.strip().lower())
        if date_of_birth:
            stmt = stmt.where(Patient.date_of_birth == validate_date_of_birth(date_of_birth))
        if phone_number:
            stmt = stmt.where(Patient.phone_number == validate_field("phone_number", phone_number))
    except ValueError as exc:
        raise ApiError(400, "bad_request", str(exc)) from None

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Patient.created_at.desc(), Patient.last_name).limit(limit).offset(offset)
    ).all()
    return list(rows), total


def find_by_phone(db: Session, phone_number: str) -> list[Patient]:
    """Active patients registered with this (already normalised) phone number, newest first."""
    return list(
        db.scalars(
            select(Patient)
            .where(Patient.phone_number == phone_number, Patient.deleted_at.is_(None))
            .order_by(Patient.created_at.desc())
        ).all()
    )


def update_patient(db: Session, patient: Patient, payload: PatientUpdate) -> Patient:
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        raise ApiError(400, "bad_request", "Provide at least one field to update.")
    for field, value in changes.items():
        setattr(patient, field, value)
    patient.updated_at = utcnow()
    db.flush()
    return patient


def soft_delete_patient(db: Session, patient: Patient) -> Patient:
    patient.deleted_at = utcnow()
    patient.updated_at = patient.deleted_at
    db.flush()
    return patient
