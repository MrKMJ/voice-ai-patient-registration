"""Tools the LLM can call during a phone call.

The voice platform (Vapi) runs the conversation; whenever the model decides to call a
tool it POSTs here (see app/routers/voice.py) and speaks whatever we return. So every
handler:

* returns a small JSON-serialisable dict for the model to read (never raises to the
  caller - failures are returned as ``{"success": false, ...}`` with an ``instruction``
  telling the model what to say next, so the caller never gets silence);
* reuses the same validators and service layer as the REST API.

Tool schemas are generated from ``TOOL_SPECS`` so the assistant config and the
dispatcher cannot drift apart.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError, format_validation_errors
from app.models import CallLog, Patient
from app.schemas import PatientCreate, PatientUpdate
from app.services import appointments as appointment_service
from app.services import patients as patient_service
from app.validators import (
    FIELD_LABELS,
    FIELD_VALIDATORS,
    PATIENT_FIELDS,
    REQUIRED_FIELDS,
    is_blank,
    normalize_spoken_email,
)

logger = logging.getLogger("app.voice")

MAX_VERIFICATION_FAILURES = 3

SYSTEM_ERROR_INSTRUCTION = (
    "Apologise briefly and tell the caller there was a technical problem saving. "
    "Try the tool once more. If it fails again, tell them a staff member will call "
    "them back to finish registration, thank them, and end the call."
)


@dataclass
class ToolContext:
    db: Session
    settings: Settings
    call_id: str | None
    caller_number: str | None = None

    def call_log(self) -> CallLog | None:
        """The row for this call, created on first use."""
        if not self.call_id:
            return None
        log = self.db.scalar(select(CallLog).where(CallLog.call_id == self.call_id))
        if log is None:
            log = CallLog(call_id=self.call_id, caller_number=self.caller_number)
            self.db.add(log)
            self.db.flush()
        return log


# --------------------------------------------------------------------------- helpers

def spoken_digits(digits: str) -> str:
    """'2125550134' -> '2 1 2, 5 5 5, 0 1 3 4' so the TTS reads digit by digit."""
    groups = [digits[:3], digits[3:6], digits[6:]]
    return ", ".join(" ".join(g) for g in groups if g)


def spoken_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def _present(field: str, value: Any) -> dict[str, str]:
    """Normalised value plus a TTS-friendly rendering for reading back to the caller."""
    if field == "date_of_birth":
        return {"normalized": value.strftime("%m/%d/%Y"), "read_back": spoken_date(value)}
    if field in ("phone_number", "emergency_contact_phone"):
        return {"normalized": value, "read_back": spoken_digits(value)}
    if field == "zip_code":
        head, _, tail = value.partition("-")
        read_back = " ".join(head) + (f", dash, {' '.join(tail)}" if tail else "")
        return {"normalized": value, "read_back": read_back}
    if field == "insurance_member_id":
        return {"normalized": value, "read_back": " ".join(value)}
    return {"normalized": value, "read_back": str(value)}


def _clean_args(args: dict[str, Any]) -> dict[str, Any]:
    """Drop empty strings/nulls the model sends for fields it has not collected."""
    cleaned = {k: v for k, v in args.items() if k in PATIENT_FIELDS and not is_blank(v)}
    if "email" in cleaned:
        cleaned["email"] = normalize_spoken_email(cleaned["email"])
    return cleaned


def _field_errors(exc: ValidationError) -> list[dict[str, str]]:
    return format_validation_errors(exc.errors())


def _reask_instruction(errors: list[dict[str, str]]) -> str:
    fields = ", ".join(FIELD_LABELS.get(e["field"], e["field"]) for e in errors)
    return (
        f"Nothing was saved. Ask the caller again ONLY for: {fields}. Explain what was wrong in "
        "one short, friendly sentence using the messages above, then confirm the corrected value "
        "and call this tool again. Do not restart the other fields."
    )


def _mask(payload: dict[str, Any], log_pii: bool) -> dict[str, Any]:
    if log_pii:
        return payload
    return {k: ("***" if k != "patient_id" else v) for k, v in payload.items()}


def _log_payload(event: str, ctx: ToolContext, payload: dict[str, Any]) -> None:
    logger.info(
        "%s call_id=%s payload=%s",
        event, ctx.call_id, json.dumps(_mask(payload, ctx.settings.log_pii), default=str),
    )


# --------------------------------------------------------------------------- handlers

def validate_field(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    field = args.get("field")
    if field not in FIELD_VALIDATORS:
        return {"valid": False, "message": f"Unknown field. Use one of: {', '.join(PATIENT_FIELDS)}."}
    value = args.get("value")
    if field == "email":
        value = normalize_spoken_email(value)
    try:
        normalized = FIELD_VALIDATORS[field](value)
    except ValueError as exc:
        return {
            "valid": False,
            "field": field,
            "message": str(exc),
            "instruction": (
                f"Tell the caller what was wrong in one short sentence and ask again for the "
                f"{FIELD_LABELS[field]} only."
            ),
        }
    result = {"valid": True, "field": field, **_present(field, normalized)}
    if field == "phone_number":
        # Chain the returning-caller check onto phone validation so it cannot be skipped
        # (in a live test the model never called lookup_patient on its own).
        result.update(_returning_caller(ctx, normalized))
    return result


def _returning_caller(ctx: ToolContext, phone: str) -> dict[str, Any]:
    """Names (only) of the most recent patient on this phone number, plus what to say."""
    matches = patient_service.find_by_phone(ctx.db, phone)
    if not matches:
        return {"found": False}
    patient = matches[0]
    return {
        "found": True,
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "last_name": patient.last_name,
        "instruction": (
            f"RETURNING CALLER. Say: \"It looks like we already have a record for "
            f"{patient.first_name} {patient.last_name}. Would you like to update your "
            "information instead?\" If they say yes, ask for their date of birth to verify "
            "identity, then use update_patient. If they say they are a different person, "
            "carry on registering a new patient."
        ),
    }


def lookup_patient(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    try:
        phone = FIELD_VALIDATORS["phone_number"](args.get("phone_number"))
    except ValueError as exc:
        return {"found": False, "valid": False, "message": str(exc)}
    return {"valid": True, **_returning_caller(ctx, phone)}


def save_patient(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    log = ctx.call_log()
    if log and log.patient_id:
        # The model retried (or the caller changed their mind) after a successful save.
        patient = ctx.db.get(Patient, log.patient_id)
        return {
            "success": True,
            "already_saved": True,
            "patient_id": str(log.patient_id),
            "first_name": patient.first_name if patient else None,
            "instruction": "This call's record is already saved. Use update_patient for any change.",
        }

    cleaned = _clean_args(args)
    try:
        payload = PatientCreate.model_validate(cleaned)
    except ValidationError as exc:
        errors = _field_errors(exc)
        logger.info("save_patient rejected call_id=%s errors=%s", ctx.call_id, errors)
        return {"success": False, "error_type": "validation", "errors": errors,
                "instruction": _reask_instruction(errors)}

    try:
        duplicates = [
            p for p in patient_service.find_by_phone(ctx.db, payload.phone_number)
            if p.date_of_birth == payload.date_of_birth
        ]
        if duplicates:
            existing = duplicates[0]
            return {
                "success": False,
                "error_type": "duplicate",
                "patient_id": str(existing.patient_id),
                "first_name": existing.first_name,
                "last_name": existing.last_name,
                "instruction": (
                    f"Nothing was saved. Say: \"It looks like we already have a record for "
                    f"{existing.first_name} {existing.last_name}. Would you like to update your "
                    "information instead?\" If yes, use update_patient with that patient_id."
                ),
            }
        patient = patient_service.create_patient(ctx.db, payload)
        if log:
            log.patient_id = patient.patient_id
        ctx.db.commit()
    except SQLAlchemyError:
        ctx.db.rollback()
        logger.exception("save_patient database failure call_id=%s", ctx.call_id)
        return {"success": False, "error_type": "system", "instruction": SYSTEM_ERROR_INSTRUCTION}

    _log_payload("patient_registered", ctx, {"patient_id": str(patient.patient_id), **payload.model_dump(mode="json")})
    return {
        "success": True,
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "instruction": (
            f"Say: \"You're all set, {patient.first_name}.\" Then offer to schedule a first "
            "appointment. If they decline, say a brief goodbye and end the call."
        ),
    }


def update_patient(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    log = ctx.call_log()
    try:
        patient_id = patient_service.parse_patient_id(str(args.get("patient_id", "")))
        patient = patient_service.get_patient(ctx.db, patient_id)
    except ApiError:
        return {"success": False, "error_type": "not_found",
                "instruction": "That record could not be found. Apologise and offer to register as a new patient."}

    # Identity check: updating an existing record needs the DOB on file, unless this very
    # call created the record (the caller just told us everything).
    created_in_this_call = bool(log and log.patient_id == patient.patient_id)
    if not created_in_this_call:
        failures = (log.meta or {}).get("verification_failures", 0) if log else 0
        if failures >= MAX_VERIFICATION_FAILURES:
            return {"success": False, "error_type": "verification_locked",
                    "instruction": "Too many failed attempts. Politely explain you cannot "
                                   "change the record on this call, suggest they contact the "
                                   "clinic directly, and end the call."}
        try:
            claimed = FIELD_VALIDATORS["date_of_birth"](args.get("verification_date_of_birth"))
        except ValueError as exc:
            return {"success": False, "error_type": "verification_needed", "message": str(exc),
                    "instruction": "Ask the caller for the date of birth on file to verify identity."}
        if claimed != patient.date_of_birth:
            if log:
                log.meta = {**(log.meta or {}), "verification_failures": failures + 1}
                ctx.db.commit()
            return {"success": False, "error_type": "verification_failed",
                    "instruction": "The date of birth does not match our records. Say so "
                                   "kindly without revealing the correct one and let them try once more."}

    changes = _clean_args({k: v for k, v in args.items() if k != "verification_date_of_birth"})
    if not changes:
        return {"success": False, "error_type": "no_changes",
                "instruction": "No fields to change were provided. Ask what they would like to update."}
    try:
        payload = PatientUpdate.model_validate(changes)
    except ValidationError as exc:
        errors = _field_errors(exc)
        return {"success": False, "error_type": "validation", "errors": errors,
                "instruction": _reask_instruction(errors)}

    try:
        patient_service.update_patient(ctx.db, patient, payload)
        if log and log.patient_id is None:
            log.patient_id = patient.patient_id
        ctx.db.commit()
    except SQLAlchemyError:
        ctx.db.rollback()
        logger.exception("update_patient database failure call_id=%s", ctx.call_id)
        return {"success": False, "error_type": "system", "instruction": SYSTEM_ERROR_INSTRUCTION}

    _log_payload("patient_updated", ctx, {"patient_id": str(patient.patient_id), **payload.model_dump(mode="json", exclude_unset=True)})
    return {
        "success": True,
        "patient_id": str(patient.patient_id),
        "first_name": patient.first_name,
        "updated_fields": sorted(changes),
        "instruction": "Confirm the update in one sentence, ask if there is anything else to change, "
                       "then offer an appointment or say goodbye and end the call.",
    }


def get_available_slots(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    slots = appointment_service.available_slots(ctx.db)
    return {
        "slots": slots,
        "instruction": "Offer at most three of these in natural speech (day and time only). "
                       "Use the slot_id of the one the caller picks with book_appointment.",
    }


def book_appointment(ctx: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    log = ctx.call_log()
    patient_id = str(args.get("patient_id", ""))
    # Only the patient registered or verified on this call can be booked.
    if not log or not log.patient_id or str(log.patient_id) != patient_id:
        return {"success": False, "error_type": "not_allowed",
                "instruction": "Appointments can only be booked after registering or updating "
                               "a record on this call. Explain that kindly."}
    try:
        appointment = appointment_service.book(
            ctx.db, log.patient_id, str(args.get("slot_id", "")), args.get("reason")
        )
        ctx.db.commit()
    except appointment_service.SlotUnavailable:
        ctx.db.rollback()
        return {"success": False, "error_type": "slot_unavailable",
                "instruction": "That time is no longer available. Call get_available_slots and offer others."}
    except SQLAlchemyError:
        ctx.db.rollback()
        logger.exception("book_appointment database failure call_id=%s", ctx.call_id)
        return {"success": False, "error_type": "system", "instruction": SYSTEM_ERROR_INSTRUCTION}
    return {
        "success": True,
        "appointment": appointment_service.slot_label(appointment.scheduled_for),
        "provider": appointment.provider,
        "instruction": "Confirm the day, time and provider in one sentence, then say goodbye and end the call.",
    }


# --------------------------------------------------------------------------- registry

def _string(description: str) -> dict[str, str]:
    return {"type": "string", "description": description}


FIELD_DESCRIPTIONS = {
    "first_name": "Caller's first name, e.g. Jane.",
    "last_name": "Caller's last name, e.g. Davis.",
    "date_of_birth": "Date of birth as MM/DD/YYYY, e.g. 03/14/1985.",
    "sex": "One of: Male, Female, Other, Decline to Answer.",
    "phone_number": "10-digit U.S. phone number (digits only is fine).",
    "email": "Email address, only if the caller provided one.",
    "address_line_1": "Street address, e.g. 123 Main Street.",
    "address_line_2": "Apartment/suite/unit, only if the caller has one.",
    "city": "City.",
    "state": "U.S. state, two-letter code or full name.",
    "zip_code": "5-digit ZIP code or ZIP+4.",
    "insurance_provider": "Insurance company name, only if provided.",
    "insurance_member_id": "Insurance member/subscriber ID, letters and digits, only if provided.",
    "preferred_language": "Preferred language; omit to default to English.",
    "emergency_contact_name": "Emergency contact's full name, only if provided.",
    "emergency_contact_phone": "Emergency contact's 10-digit phone number, only if provided.",
}

_PATIENT_PROPERTIES = {f: _string(FIELD_DESCRIPTIONS[f]) for f in PATIENT_FIELDS}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], dict[str, Any]]


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required}


TOOL_SPECS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "validate_field",
            "Check one value the caller gave (date of birth, phone, state, ZIP, email, member ID, "
            "emergency phone) BEFORE moving on. Returns valid/invalid plus the normalised value and "
            "a read_back string; if invalid, the message says what to re-ask.",
            _schema(
                {
                    "field": {"type": "string", "enum": list(PATIENT_FIELDS),
                              "description": "Which field the value belongs to."},
                    "value": _string("The value exactly as the caller said it."),
                },
                ["field", "value"],
            ),
            validate_field,
        ),
        ToolSpec(
            "lookup_patient",
            "Look up an existing patient by phone number to detect a returning caller. Call this as "
            "soon as you have a valid phone number.",
            _schema({"phone_number": _string(FIELD_DESCRIPTIONS["phone_number"])}, ["phone_number"]),
            lookup_patient,
        ),
        ToolSpec(
            "save_patient",
            "Save a NEW patient record. Call only after reading every collected field back and the "
            "caller explicitly confirming. Returns success, or per-field errors to re-ask.",
            _schema(_PATIENT_PROPERTIES, list(REQUIRED_FIELDS)),
            save_patient,
        ),
        ToolSpec(
            "update_patient",
            "Update an EXISTING patient (returning caller who agreed to update, or a correction "
            "after saving). Send only the fields that change. For a returning caller, also send "
            "verification_date_of_birth.",
            _schema(
                {
                    "patient_id": _string("patient_id from lookup_patient or save_patient."),
                    "verification_date_of_birth": _string(
                        "Date of birth the caller states, MM/DD/YYYY, used to verify identity."
                    ),
                    **_PATIENT_PROPERTIES,
                },
                ["patient_id"],
            ),
            update_patient,
        ),
        ToolSpec(
            "get_available_slots",
            "List upcoming first-appointment slots (mock schedule).",
            _schema({}, []),
            get_available_slots,
        ),
        ToolSpec(
            "book_appointment",
            "Book one of the slots from get_available_slots for the patient on this call.",
            _schema(
                {
                    "patient_id": _string("patient_id of the caller's record."),
                    "slot_id": _string("slot_id exactly as returned by get_available_slots."),
                    "reason": _string("Optional short reason, defaults to a new patient visit."),
                },
                ["patient_id", "slot_id"],
            ),
            book_appointment,
        ),
    )
}


def run_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch one tool call. Never raises: the caller must always hear something."""
    spec = TOOL_SPECS.get(name)
    if spec is None:
        return {"success": False, "error_type": "unknown_tool", "message": f"Unknown tool {name}."}
    try:
        return spec.handler(ctx, args if isinstance(args, dict) else {})
    except Exception:  # noqa: BLE001 - last line of defence for a live phone call
        logger.exception("tool %s crashed call_id=%s", name, ctx.call_id)
        try:
            ctx.db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return {"success": False, "error_type": "system", "instruction": SYSTEM_ERROR_INSTRUCTION}
