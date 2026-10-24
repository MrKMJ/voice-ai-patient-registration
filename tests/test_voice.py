"""Drives the webhook exactly as Vapi would, simulating whole phone calls."""
import json

import pytest
from sqlalchemy import select

from app.models import CallLog
from app.voice.assistant import build_assistant_config
from app.voice.tools import TOOL_SPECS

HEADERS = {"X-Vapi-Secret": "s3cret"}

SAVE_ARGS = {
    "first_name": "Jane", "last_name": "Davis", "date_of_birth": "03/14/1985", "sex": "Female",
    "phone_number": "2125550134", "address_line_1": "123 Main Street", "address_line_2": "",
    "city": "Brooklyn", "state": "NY", "zip_code": "11201", "email": "",
}


def tool(client, name, args, call_id="call-1", headers=HEADERS):
    payload = {"message": {
        "type": "tool-calls",
        "call": {"id": call_id, "customer": {"number": "+12125550134"}},
        "toolCallList": [{"id": "tc1", "type": "function",
                          "function": {"name": name, "arguments": args}}],
    }}
    res = client.post("/voice/webhook", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    (result,) = res.json()["results"]
    assert result["toolCallId"] == "tc1" and result["name"] == name
    return json.loads(result["result"])


def test_webhook_requires_secret(client):
    body = {"message": {"type": "status-update", "call": {"id": "x"}}}
    assert client.post("/voice/webhook", json=body).status_code == 401
    assert client.post("/voice/webhook", json=body, headers={"X-Vapi-Secret": "no"}).status_code == 401
    assert client.post("/voice/webhook", json=body, headers=HEADERS).status_code == 200
    bearer = {"Authorization": "Bearer s3cret"}
    assert client.post("/voice/webhook", json=body, headers=bearer).status_code == 200


def test_production_without_secret_is_refused(tmp_path):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    cfg = Settings(database_url=f"sqlite:///{tmp_path / 'p.db'}", environment="production", _env_file=None)
    with TestClient(create_app(cfg)) as c:
        res = c.post("/voice/webhook", json={"message": {"type": "status-update"}})
        assert res.status_code == 503


def test_full_registration_call(client):
    # 1. Caller gives a phone number; nobody on file yet.
    assert tool(client, "lookup_patient", {"phone_number": "212-555-0134"})["found"] is False
    # 2. Field checks return a read_back suitable for TTS.
    dob = tool(client, "validate_field", {"field": "date_of_birth", "value": "03/14/1985"})
    assert dob["valid"] and dob["read_back"] == "March 14, 1985"
    phone = tool(client, "validate_field", {"field": "phone_number", "value": "2125550134"})
    assert phone["read_back"] == "2 1 2, 5 5 5, 0 1 3 4"
    # 3. Save after confirmation.
    saved = tool(client, "save_patient", SAVE_ARGS)
    assert saved["success"] and saved["first_name"] == "Jane"
    # 4. Persisted and visible via REST, as on a later call.
    rows = client.get("/patients?last_name=Davis").json()["data"]
    assert len(rows) == 1 and rows[0]["patient_id"] == saved["patient_id"]
    # 5. End-of-call report links transcript to the patient.
    client.post("/voice/webhook", headers=HEADERS, json={"message": {
        "type": "end-of-call-report", "endedReason": "assistant-ended-call",
        "call": {"id": "call-1"}, "artifact": {"transcript": "AI: Hi\nUser: Jane"},
        "analysis": {"summary": "Registered Jane Davis."}}})
    calls = client.get(f"/patients/{saved['patient_id']}/calls").json()["data"]
    assert calls[0]["transcript"].startswith("AI: Hi") and calls[0]["summary"] == "Registered Jane Davis."


def test_invalid_values_get_field_specific_reprompts(client):
    bad_phone = tool(client, "validate_field", {"field": "phone_number", "value": "555"})
    assert bad_phone["valid"] is False and "10 digits" in bad_phone["message"]
    assert "phone number" in bad_phone["instruction"]

    bad_dob = tool(client, "validate_field", {"field": "date_of_birth", "value": "12/25/2999"})
    assert bad_dob["valid"] is False and "future" in bad_dob["message"]

    result = tool(client, "save_patient", {**SAVE_ARGS, "phone_number": "555", "date_of_birth": "12/25/2999"})
    assert result["success"] is False and result["error_type"] == "validation"
    assert {e["field"] for e in result["errors"]} == {"phone_number", "date_of_birth"}
    assert "ONLY" in result["instruction"]
    assert client.get("/patients").json()["data"] == []  # nothing half-saved


def test_missing_required_field_reported(client):
    args = {k: v for k, v in SAVE_ARGS.items() if k != "zip_code"}
    result = tool(client, "save_patient", args)
    assert [e["field"] for e in result["errors"]] == ["zip_code"]


def test_retry_after_success_does_not_duplicate(client):
    first = tool(client, "save_patient", SAVE_ARGS)
    again = tool(client, "save_patient", SAVE_ARGS)
    assert again["success"] and again["already_saved"] and again["patient_id"] == first["patient_id"]
    assert len(client.get("/patients").json()["data"]) == 1


def test_returning_caller_flow_with_verification(client):
    patient_id = tool(client, "save_patient", SAVE_ARGS, call_id="call-1")["patient_id"]

    # Second call, same phone number.
    found = tool(client, "lookup_patient", {"phone_number": "2125550134"}, call_id="call-2")
    assert found["found"] and found["first_name"] == "Jane" and found["patient_id"] == patient_id
    assert "already have a record for Jane Davis" in found["instruction"]
    assert "date_of_birth" not in found  # no PII beyond the name

    no_dob = tool(client, "update_patient", {"patient_id": patient_id, "city": "Queens"}, call_id="call-2")
    assert no_dob["error_type"] == "verification_needed"

    wrong = tool(client, "update_patient", {"patient_id": patient_id, "city": "Queens",
                                            "verification_date_of_birth": "01/01/1990"}, call_id="call-2")
    assert wrong["error_type"] == "verification_failed"
    assert client.get(f"/patients/{patient_id}").json()["data"]["city"] == "Brooklyn"

    ok = tool(client, "update_patient", {"patient_id": patient_id, "city": "Queens",
                                         "verification_date_of_birth": "03/14/1985"}, call_id="call-2")
    assert ok["success"] and ok["updated_fields"] == ["city"]
    assert client.get(f"/patients/{patient_id}").json()["data"]["city"] == "Queens"


def test_verification_locks_after_repeated_failures(client):
    patient_id = tool(client, "save_patient", SAVE_ARGS, call_id="call-1")["patient_id"]
    for _ in range(3):
        tool(client, "update_patient", {"patient_id": patient_id, "city": "X",
                                        "verification_date_of_birth": "01/01/2001"}, call_id="call-9")
    locked = tool(client, "update_patient", {"patient_id": patient_id, "city": "X",
                                             "verification_date_of_birth": "03/14/1985"}, call_id="call-9")
    assert locked["error_type"] == "verification_locked"


def test_correction_after_save_in_same_call_needs_no_dob(client):
    patient_id = tool(client, "save_patient", SAVE_ARGS)["patient_id"]
    res = tool(client, "update_patient", {"patient_id": patient_id, "last_name": "Davies"})
    assert res["success"]


def test_duplicate_save_from_new_call_offers_update(client):
    tool(client, "save_patient", SAVE_ARGS, call_id="call-1")
    dup = tool(client, "save_patient", SAVE_ARGS, call_id="call-2")
    assert dup["success"] is False and dup["error_type"] == "duplicate"
    assert "Would you like to update" in dup["instruction"]
    assert len(client.get("/patients").json()["data"]) == 1


def test_shared_phone_different_person_can_register(client):
    tool(client, "save_patient", SAVE_ARGS, call_id="call-1")
    other = {**SAVE_ARGS, "first_name": "Tom", "date_of_birth": "05/05/1980"}
    assert tool(client, "save_patient", other, call_id="call-2")["success"]


def test_database_failure_returns_speakable_error(client, monkeypatch):
    from app.services import patients as svc
    from sqlalchemy.exc import OperationalError

    def boom(*a, **k):
        raise OperationalError("INSERT", {}, Exception("db down"))

    monkeypatch.setattr(svc, "create_patient", boom)
    result = tool(client, "save_patient", SAVE_ARGS)
    assert result["success"] is False and result["error_type"] == "system"
    assert "technical problem" in result["instruction"]

    monkeypatch.undo()  # database comes back; the retry succeeds
    assert tool(client, "save_patient", SAVE_ARGS)["success"]


def test_unexpected_tool_crash_never_raises(client, monkeypatch):
    from app.voice import tools
    monkeypatch.setitem(tools.TOOL_SPECS, "lookup_patient",
                        tools.ToolSpec("lookup_patient", "", {}, lambda c, a: 1 / 0))
    result = tool(client, "lookup_patient", {"phone_number": "2125550134"})
    assert result["success"] is False and "instruction" in result


def test_unknown_tool_and_string_arguments(client):
    assert tool(client, "does_not_exist", {})["error_type"] == "unknown_tool"
    payload = {"message": {"type": "tool-calls", "call": {"id": "c"}, "toolCalls": [
        {"id": "t", "function": {"name": "validate_field",
                                 "arguments": json.dumps({"field": "state", "value": "texas"})}}]}}
    res = client.post("/voice/webhook", json=payload, headers=HEADERS)
    assert json.loads(res.json()["results"][0]["result"])["normalized"] == "TX"


def test_dropped_call_is_logged_without_patient(client, settings):
    res = client.post("/voice/webhook", headers=HEADERS, json={"message": {
        "type": "end-of-call-report", "endedReason": "customer-ended-call",
        "call": {"id": "dropped", "customer": {"number": "+15551230000"}},
        "artifact": {"transcript": "AI: Hi\nUser: My name is"}}})
    assert res.status_code == 200
    with client.app.state.session_factory() as db:
        log = db.scalar(select(CallLog).where(CallLog.call_id == "dropped"))
        assert log.patient_id is None and log.ended_reason == "customer-ended-call"


def test_appointment_booking(client):
    patient_id = tool(client, "save_patient", SAVE_ARGS)["patient_id"]
    slots = tool(client, "get_available_slots", {})["slots"]
    assert 3 <= len(slots) <= 6
    booked = tool(client, "book_appointment", {"patient_id": patient_id, "slot_id": slots[0]["slot_id"]})
    assert booked["success"] and booked["appointment"] == slots[0]["label"]
    assert slots[0]["slot_id"] not in {s["slot_id"] for s in tool(client, "get_available_slots", {})["slots"]}
    listed = client.get(f"/patients/{patient_id}/appointments").json()["data"]
    assert len(listed) == 1


def test_appointment_rules(client):
    patient_id = tool(client, "save_patient", SAVE_ARGS, call_id="call-1")["patient_id"]
    slot = tool(client, "get_available_slots", {})["slots"][0]["slot_id"]
    # A different call cannot book for this patient.
    assert tool(client, "book_appointment", {"patient_id": patient_id, "slot_id": slot},
                call_id="call-other")["error_type"] == "not_allowed"
    # Made-up slots are refused.
    bad = tool(client, "book_appointment", {"patient_id": patient_id, "slot_id": "2000-01-01T09:00"},
               call_id="call-1")
    assert bad["error_type"] == "slot_unavailable"


def test_assistant_config_matches_tools(settings):
    cfg = build_assistant_config(settings, public_base_url="https://x.example/", webhook_secret="abc")
    names = [t["function"]["name"] for t in cfg["model"]["tools"] if t["type"] == "function"]
    assert names == list(TOOL_SPECS)
    assert {"type": "endCall"} in cfg["model"]["tools"]
    assert cfg["server"]["url"] == "https://x.example/voice/webhook"
    assert cfg["server"]["headers"] == {"X-Vapi-Secret": "abc"}
    prompt = cfg["model"]["messages"][0]["content"]
    assert "{" not in prompt  # all template placeholders resolved
    save = next(t for t in cfg["model"]["tools"] if t.get("function", {}).get("name") == "save_patient")
    assert "first_name" in save["function"]["parameters"]["required"]
    json.dumps(cfg)  # serialisable
