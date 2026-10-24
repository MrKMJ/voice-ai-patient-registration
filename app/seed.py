"""Optional demo data (fictional people, 555 phone numbers)."""
from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Patient
from app.schemas import PatientCreate
from app.services import patients as service

logger = logging.getLogger("app.seed")

SEED_PATIENTS = [
    {
        "first_name": "Alex", "last_name": "Rivera", "date_of_birth": "04/12/1988", "sex": "Male",
        "phone_number": "2125550101", "email": "alex.rivera@example.com",
        "address_line_1": "482 Maple Avenue", "address_line_2": "Apt 3B", "city": "Brooklyn",
        "state": "NY", "zip_code": "11201", "insurance_provider": "Blue Cross Blue Shield",
        "insurance_member_id": "XJK482910", "preferred_language": "English",
        "emergency_contact_name": "Marta Rivera", "emergency_contact_phone": "2125550102",
    },
    {
        "first_name": "Priya", "last_name": "Natarajan", "date_of_birth": "11/03/1994",
        "sex": "Female", "phone_number": "4155550134", "address_line_1": "90 Embarcadero Street",
        "city": "San Francisco", "state": "CA", "zip_code": "94105", "preferred_language": "Tamil",
    },
]


def seed_demo_data(session_factory: sessionmaker[Session]) -> int:
    """Insert the demo patients if the table is empty. Returns how many were added."""
    with session_factory() as db:
        if db.scalar(select(func.count()).select_from(Patient)):
            return 0
        for record in SEED_PATIENTS:
            service.create_patient(db, PatientCreate.model_validate(record))
        db.commit()
    logger.info("seeded %d demo patients", len(SEED_PATIENTS))
    return len(SEED_PATIENTS)
