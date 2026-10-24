"""Mock appointment scheduling (bonus feature).

Availability is generated, not stored: the next few business days at fixed times for
one provider, minus anything already booked. ``scheduled_for`` is clinic-local
wall-clock time, so no timezone arithmetic is needed beyond finding "today".
"""
from __future__ import annotations

import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Appointment

CLINIC_TZ = ZoneInfo("America/New_York")
PROVIDER = "Dr. Anita Patel"
SLOT_TIMES = (time(9, 0), time(11, 30), time(14, 0), time(15, 30))
DAYS_AHEAD = 3  # business days offered, starting tomorrow
SLOTS_PER_DAY_OFFERED = 2


def slot_label(when: datetime) -> str:
    hour = when.strftime("%I").lstrip("0")
    return f"{when:%A, %B} {when.day} at {hour}:{when:%M %p}"


def available_slots(db: Session, *, now: datetime | None = None) -> list[dict[str, str]]:
    local_today = (now or datetime.now(CLINIC_TZ)).date()
    days = []
    cursor = local_today
    while len(days) < DAYS_AHEAD:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # Monday-Friday
            days.append(cursor)

    booked = set(
        db.scalars(
            select(Appointment.scheduled_for).where(
                Appointment.provider == PROVIDER,
                Appointment.status == "scheduled",
                Appointment.scheduled_for >= datetime.combine(days[0], time.min),
            )
        ).all()
    )
    slots = []
    for day in days:
        offered = 0
        for slot_time in SLOT_TIMES:
            when = datetime.combine(day, slot_time)
            if when in booked or offered >= SLOTS_PER_DAY_OFFERED:
                continue
            slots.append(
                {"slot_id": when.isoformat(timespec="minutes"), "label": slot_label(when),
                 "provider": PROVIDER}
            )
            offered += 1
    return slots


class SlotUnavailable(Exception):
    pass


def book(db: Session, patient_id: uuid.UUID, slot_id: str, reason: str | None) -> Appointment:
    """Book a slot previously offered by ``available_slots``. Raises SlotUnavailable."""
    if slot_id not in {s["slot_id"] for s in available_slots(db)}:
        raise SlotUnavailable(slot_id)
    appointment = Appointment(
        patient_id=patient_id,
        scheduled_for=datetime.fromisoformat(slot_id),
        provider=PROVIDER,
        reason=(reason or "New patient visit")[:200],
    )
    db.add(appointment)
    try:
        db.flush()
    except IntegrityError:  # lost a race for the same slot
        db.rollback()
        raise SlotUnavailable(slot_id) from None
    return appointment
