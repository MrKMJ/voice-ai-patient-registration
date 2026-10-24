"""REST API for patient records (spec section 4)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Appointment, CallLog
from app.schemas import (
    AppointmentOut,
    CallLogOut,
    Envelope,
    PatientCreate,
    PatientOut,
    PatientUpdate,
)
from app.security import require_api_key
from app.services import patients as service

router = APIRouter(prefix="/patients", tags=["patients"], dependencies=[Depends(require_api_key)])


@router.get("", response_model=Envelope[list[PatientOut]])
def list_patients(
    last_name: str | None = Query(None, description="Case-insensitive exact match"),
    date_of_birth: str | None = Query(None, description="MM/DD/YYYY"),
    phone_number: str | None = Query(None, description="Any common U.S. format"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    rows, total = service.list_patients(
        db, last_name=last_name, date_of_birth=date_of_birth, phone_number=phone_number,
        limit=limit, offset=offset,
    )
    return Envelope(
        data=[PatientOut.model_validate(r) for r in rows],
        meta={"total": total, "limit": limit, "offset": offset},
    )


@router.post("", status_code=201, response_model=Envelope[PatientOut])
def create_patient(payload: PatientCreate, db: Session = Depends(get_db)):
    patient = service.create_patient(db, payload)
    db.commit()
    return Envelope(data=PatientOut.model_validate(patient))


@router.get("/{patient_id}", response_model=Envelope[PatientOut])
def get_patient(patient_id: str, db: Session = Depends(get_db)):
    patient = service.get_patient(db, service.parse_patient_id(patient_id))
    return Envelope(data=PatientOut.model_validate(patient))


@router.put("/{patient_id}", response_model=Envelope[PatientOut])
def update_patient(patient_id: str, payload: PatientUpdate, db: Session = Depends(get_db)):
    patient = service.get_patient(db, service.parse_patient_id(patient_id))
    service.update_patient(db, patient, payload)
    db.commit()
    return Envelope(data=PatientOut.model_validate(patient))


@router.delete("/{patient_id}", response_model=Envelope[PatientOut])
def delete_patient(patient_id: str, db: Session = Depends(get_db)):
    """Soft delete: sets deleted_at; the row is kept and hidden from every read."""
    patient = service.get_patient(db, service.parse_patient_id(patient_id))
    service.soft_delete_patient(db, patient)
    db.commit()
    return Envelope(data=PatientOut.model_validate(patient))


@router.get("/{patient_id}/calls", response_model=Envelope[list[CallLogOut]])
def patient_calls(patient_id: str, db: Session = Depends(get_db)):
    """Transcripts/summaries of the calls that created or updated this patient."""
    patient = service.get_patient(db, service.parse_patient_id(patient_id))
    rows = db.scalars(
        select(CallLog)
        .where(CallLog.patient_id == patient.patient_id)
        .order_by(CallLog.created_at.desc())
    ).all()
    return Envelope(data=[CallLogOut.model_validate(r) for r in rows])


@router.get("/{patient_id}/appointments", response_model=Envelope[list[AppointmentOut]])
def patient_appointments(patient_id: str, db: Session = Depends(get_db)):
    patient = service.get_patient(db, service.parse_patient_id(patient_id))
    rows = db.scalars(
        select(Appointment)
        .where(Appointment.patient_id == patient.patient_id)
        .order_by(Appointment.scheduled_for)
    ).all()
    return Envelope(data=[AppointmentOut.model_validate(r) for r in rows])
