"""Check the running system against the challenge document, requirement by requirement.

    python scripts/verify_requirements.py --base-url https://your-app.up.railway.app --api-key KEY
    python scripts/verify_requirements.py --base-url http://localhost:8000          # local

Reads API_KEY, VAPI_WEBHOOK_SECRET, VAPI_API_KEY and VAPI_ASSISTANT_ID from the environment / .env.
Vapi checks run when VAPI_API_KEY and VAPI_ASSISTANT_ID are set (use --skip-vapi to omit them).

It creates ONE clearly-labelled fictional patient to exercise create/update/delete, then soft-deletes
it. Things that need a human on a real phone (how natural the conversation sounds) cannot be
automated and are listed at the end.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import string
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402

UA = "patient-voice-agent-verify/1.0"
results: list[tuple[str, str, bool, str]] = []  # (section, requirement, passed, detail)
section = ""


def http(method, url, *, body=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": UA, "Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode()
            try:
                return response.status, (json.loads(text) if text else None)
            except json.JSONDecodeError:  # e.g. the HTML docs page
                return response.status, text
    except urllib.error.HTTPError as exc:
        text = exc.read().decode()
        try:
            return exc.code, json.loads(text)
        except json.JSONDecodeError:
            return exc.code, text


def check(requirement: str, passed: bool, detail: str = "") -> bool:
    results.append((section, requirement, bool(passed), detail))
    return bool(passed)


def main() -> int:
    global section
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--skip-vapi", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    base = args.base_url.rstrip("/")
    key = args.api_key if args.api_key is not None else settings.api_key
    auth = {"X-API-Key": key} if key else {}
    P = "/patients"

    # ------------------------------------------------------------------ deployment
    section = "Deployment"
    status, body = http("GET", base + "/health")
    check("Service is live and reachable over HTTPS" if base.startswith("https") else "Service is live",
          status == 200 and body["data"]["status"] == "ok", f"GET /health -> {status}")
    status, _ = http("GET", base + "/docs")
    check("Interactive API docs available", status == 200, f"/docs -> {status}")

    # ------------------------------------------------------------------ data model + REST
    section = "REST API (spec 4) and data model (spec 2)"
    valid = {
        "first_name": "Verify",
        "last_name": "Check" + "".join(random.choices(string.ascii_lowercase, k=6)),  # letters only
        "date_of_birth": "03/14/1985", "sex": "Female", "phone_number": "(212) 555-0177",
        "address_line_1": "1 Test Street", "city": "Brooklyn", "state": "NY", "zip_code": "11201",
    }
    status, created = http("POST", base + P, body=valid, headers=auth)
    data = (created or {}).get("data") or {}
    pid = data.get("patient_id", "")
    try:
        uuid.UUID(pid)
        is_uuid = True
    except ValueError:
        is_uuid = False
    check("POST /patients returns 201 with the created record", status == 201 and bool(data), f"-> {status}")
    if not pid:
        sys.exit(f"Cannot continue: creating the test patient failed -> {status} {created}")
    check("patient_id is an auto-generated UUID", is_uuid, pid)
    check("Envelope shape {data, error:null} on success", created and created.get("error") is None and "data" in created)
    expected = {"patient_id", "first_name", "last_name", "date_of_birth", "sex", "phone_number", "email",
                "address_line_1", "address_line_2", "city", "state", "zip_code", "insurance_provider",
                "insurance_member_id", "preferred_language", "emergency_contact_name",
                "emergency_contact_phone", "created_at", "updated_at"}
    check("Record exposes all 16 demographic fields + timestamps + id", expected <= set(data),
          f"missing: {sorted(expected - set(data))}")
    check("preferred_language defaults to English", data.get("preferred_language") == "English")
    check("Optional fields may be omitted (email, address_line_2, insurance, emergency contact)",
          all(data.get(f) is None for f in ("email", "address_line_2", "insurance_provider",
                                            "insurance_member_id", "emergency_contact_name",
                                            "emergency_contact_phone")))
    check("Phone normalised to 10 digits", data.get("phone_number") == "2125550177", str(data.get("phone_number")))
    check("date_of_birth rendered MM/DD/YYYY", data.get("date_of_birth") == "03/14/1985")
    check("created_at/updated_at auto-generated in UTC",
          str(data.get("created_at", "")).endswith("Z") and str(data.get("updated_at", "")).endswith("Z"))

    status, one = http("GET", f"{base}{P}/{pid}", headers=auth)
    check("GET /patients/:id returns the record (200)", status == 200 and one["data"]["patient_id"] == pid)
    status, lst = http("GET", base + P, headers=auth)
    check("GET /patients lists patients (200)", status == 200 and isinstance(lst["data"], list))
    status, f = http("GET", f"{base}{P}?last_name={valid['last_name'].lower()}", headers=auth)
    check("Filter ?last_name= (case-insensitive)", status == 200 and [p["patient_id"] for p in f["data"]] == [pid])
    status, f = http("GET", f"{base}{P}?date_of_birth=03/14/1985", headers=auth)
    check("Filter ?date_of_birth=", status == 200 and pid in [p["patient_id"] for p in f["data"]])
    status, f = http("GET", f"{base}{P}?phone_number=212-555-0177", headers=auth)
    check("Filter ?phone_number= (any U.S. format)", status == 200 and pid in [p["patient_id"] for p in f["data"]])

    before = data.get("updated_at")
    status, upd = http("PUT", f"{base}{P}/{pid}", body={"city": "Queens"}, headers=auth)
    ud = (upd or {}).get("data") or {}
    check("PUT /patients/:id partial update (200)", status == 200 and ud.get("city") == "Queens"
          and ud.get("first_name") == "Verify")
    check("updated_at changes on modification, created_at does not",
          ud.get("updated_at", "") > (before or "") and ud.get("created_at") == data.get("created_at"))

    # validation rules (server-side), each must be rejected with 422 and a field-specific message
    section = "Validation rules (spec 2) - enforced server-side"
    bad_cases = {
        "3-digit phone number": {"phone_number": "555"},
        "future date of birth": {"date_of_birth": "01/01/2999"},
        "impossible date (02/30)": {"date_of_birth": "02/30/1990"},
        "sex outside the enum": {"sex": "Robot"},
        "invalid state": {"state": "ZZ"},
        "invalid ZIP": {"zip_code": "1234"},
        "digits in a name": {"first_name": "R2D2"},
        "name over 50 characters": {"last_name": "x" * 51},
        "invalid email": {"email": "not-an-email"},
        "emergency phone with 7 digits": {"emergency_contact_phone": "5550123"},
        "non-alphanumeric insurance member ID": {"insurance_member_id": "AB!12"},
    }
    for label, override in bad_cases.items():
        status, resp = http("POST", base + P, body={**valid, **override}, headers=auth)
        field = next(iter(override))
        fields = [d["field"] for d in ((resp or {}).get("error") or {}).get("details", [])] if isinstance(resp, dict) else []
        check(f"Rejects {label} (422, names the field)", status == 422 and field in fields, f"-> {status} {fields}")
    for field in ("first_name", "last_name", "date_of_birth", "sex", "phone_number",
                  "address_line_1", "city", "state", "zip_code"):
        status, resp = http("POST", base + P, body={k: v for k, v in valid.items() if k != field}, headers=auth)
        check(f"Required field '{field}' enforced", status == 422, f"-> {status}")
    ok_variants = {"hyphen/apostrophe names": {"first_name": "Mary-Jane", "last_name": "O'Brien"},
                   "ZIP+4": {"zip_code": "11201-1234"}}
    for label, override in ok_variants.items():
        status, resp = http("POST", base + P, body={**valid, **override, "phone_number": "2125550178"}, headers=auth)
        if status == 201:
            http("DELETE", f"{base}{P}/{resp['data']['patient_id']}", headers=auth)
        check(f"Accepts valid {label}", status == 201, f"-> {status}")

    # status codes, envelope, delete
    section = "API standards"
    status, resp = http("POST", base + P, raw=b"{not json", headers=auth)
    check("400 for malformed JSON", status == 400, f"-> {status}")
    status, resp = http("GET", f"{base}{P}/not-a-uuid", headers=auth)
    check("400 for a malformed patient_id", status == 400, f"-> {status}")
    status, resp = http("GET", f"{base}{P}/{uuid.uuid4()}", headers=auth)
    check("404 for an unknown patient, error envelope {data:null, error:{...}}",
          status == 404 and resp.get("data") is None and resp["error"]["code"] == "not_found")
    status, resp = http("POST", base + P, body={**valid, "phone_number": "1"}, headers=auth)
    check("422 body carries per-field details", status == 422 and resp["error"]["details"])
    status, resp = http("POST", base + P, body={**valid, "first_name": "<script>alert(1)</script>"}, headers=auth)
    stored = (resp or {}).get("data", {}).get("first_name", "") if status == 201 else ""
    if status == 201:
        http("DELETE", f"{base}{P}/{resp['data']['patient_id']}", headers=auth)
    check("Basic input sanitisation (angle brackets stripped / rejected)", "<" not in stored and ">" not in stored,
          f"-> {status} {stored!r}")

    status, deleted = http("DELETE", f"{base}{P}/{pid}", headers=auth)
    check("DELETE /patients/:id soft-deletes (sets deleted_at)", status == 200 and deleted["data"]["deleted_at"])
    status, _ = http("GET", f"{base}{P}/{pid}", headers=auth)
    check("Soft-deleted patient is hidden from reads (404)", status == 404, f"-> {status}")

    section = "Security"
    if key:
        status, _ = http("GET", base + P)
        check("REST API refuses requests without the API key (401)", status == 401, f"-> {status}")
    else:
        check("REST API key configured (optional)", True, "API_KEY not set; endpoints are open")
    status, _ = http("POST", base + "/voice/webhook", body={"message": {"type": "status-update"}})
    check("Voice webhook refuses unauthenticated calls (401)", status == 401, f"-> {status}")

    # ------------------------------------------------------------------ voice agent <-> database
    secret = settings.vapi_webhook_secret
    section = "Voice agent <-> database integration (spec 5), via the live webhook"
    if secret:
        hdr = {"X-Vapi-Secret": secret}
        call_id = f"verify-{uuid.uuid4()}"

        def tool(name, arguments):
            status, resp = http("POST", base + "/voice/webhook", headers=hdr, body={"message": {
                "type": "tool-calls", "call": {"id": call_id},
                "toolCallList": [{"id": "t", "function": {"name": name, "arguments": arguments}}]}})
            return status, (json.loads(resp["results"][0]["result"]) if status == 200 else {})

        status, r = tool("validate_field", {"field": "phone_number", "value": "555"})
        check("Invalid phone -> specific re-prompt message for that field",
              status == 200 and r.get("valid") is False and "10 digits" in r.get("message", "")
              and "phone number" in r.get("instruction", ""), str(r.get("message")))
        status, r = tool("validate_field", {"field": "date_of_birth", "value": "01/01/2999"})
        check("Future date of birth -> specific re-prompt message",
              r.get("valid") is False and "future" in r.get("message", ""), str(r.get("message")))
        status, r = tool("save_patient", {"first_name": "Verify", "last_name": "Check", "phone_number": "555"})
        check("save_patient with bad/missing data saves nothing and lists the fields to re-ask",
              r.get("success") is False and r.get("error_type") == "validation" and r.get("errors"))
        status, r = tool("validate_field", {"field": "phone_number", "value": "212-555-0101"})
        check("Bonus: returning caller recognised by phone number (seed patient)",
              r.get("found") is True and "already have a record" in r.get("instruction", ""),
              "requires SEED_DEMO_DATA=true" if not r.get("found") else "")
        status, r = tool("get_available_slots", {})
        check("Bonus: appointment slots offered", bool(r.get("slots")), f"{len(r.get('slots', []))} slots")
    else:
        check("Webhook checks", False, "VAPI_WEBHOOK_SECRET not set; cannot call the webhook")

    # ------------------------------------------------------------------ Vapi: telephony + prompt
    section = "Telephony and voice agent (spec 1)"
    vapi_key, assistant_id = settings.vapi_api_key, settings.vapi_assistant_id
    if args.skip_vapi or not (vapi_key and assistant_id):
        check("Vapi assistant inspected", False, "VAPI_API_KEY / VAPI_ASSISTANT_ID not set (or --skip-vapi)")
    else:
        vh = {"Authorization": f"Bearer {vapi_key}"}
        status, assistant = http("GET", f"https://api.vapi.ai/assistant/{assistant_id}", headers=vh)
        status2, numbers = http("GET", "https://api.vapi.ai/phone-number", headers=vh)
        attached = [n for n in (numbers if isinstance(numbers, list) else [])
                    if n.get("assistantId") == assistant_id]
        check("A real dialable U.S. phone number is attached to the assistant",
              bool(attached) and str(attached[0].get("number", "")).startswith("+1"),
              str(attached[0].get("number")) if attached else "none")
        if isinstance(assistant, dict) and "model" in assistant:
            model = assistant["model"]
            prompt = model["messages"][0]["content"]
            tools = {t.get("function", {}).get("name"): t for t in model.get("tools", [])}
            check("LLM-powered (not an IVR menu)", bool(model.get("model")),
                  f"{model.get('provider')}/{model.get('model')}")
            check("Webhook URL points at this deployment",
                  assistant.get("server", {}).get("url") == base + "/voice/webhook",
                  str(assistant.get("server", {}).get("url")))
            check("Prompt: reads back all information and asks to confirm or correct",
                  "Is all of that correct" in prompt and "NEVER call `save_patient` before" in prompt)
            check("Prompt: offers optional insurance / emergency contact / language",
                  "I can also collect your email" in prompt and "preferred language" in prompt)
            check("Prompt: re-prompts for the specific invalid field",
                  "re-ask for THAT field only" in prompt)
            check("Prompt: handles corrections, interruptions and start-over",
                  all(s in prompt for s in ("Corrections", "interrupts", "start over")))
            check("Prompt: confirmation phrase 'You're all set, [First Name]'", "You're all set, [First Name]" in prompt)
            check("Prompt: duplicate-caller phrase from the spec",
                  "already have a record for [First] [Last]" in prompt)
            check("Agent can end the call gracefully (end-call tool)", any(t.get("type") == "endCall" for t in model["tools"]))
            save = tools.get("save_patient", {}).get("function", {}).get("parameters", {})
            check("save_patient exposes all 16 data fields", len(save.get("properties", {})) == 16,
                  f"{len(save.get('properties', {}))} fields")
            check("save_patient marks exactly the 9 required fields required", len(save.get("required", [])) == 9)
        else:
            check("Vapi assistant readable", False, str(assistant)[:200])

    # ------------------------------------------------------------------ documentation
    section = "README and code deliverables"
    root = Path(__file__).resolve().parent.parent
    readme = (root / "README.md").read_text(encoding="utf-8").lower()
    for label, needle in {"setup instructions": "local setup", "architecture description": "## architecture",
                          "tech stack justification": "tech stack and why", "environment variables": "## environment variables",
                          "known limitations / trade-offs": "known limitations", "'Next steps' section": "## next steps",
                          "prompt engineering documented": "prompt engineering"}.items():
        check(f"README has: {label}", needle in readme)
    check("Phone number listed in README", bool(re.search(r"\+1 \(\d{3}\) \d{3}-\d{4}", readme)))
    check("API base URL listed in README", base.split("//")[1].lower() in readme)
    check("LLM system prompt is in the repo and commented",
          (root / "app/voice/prompt.py").exists() and '"""' in (root / "app/voice/prompt.py").read_text(encoding="utf-8"))
    check(".env is git-ignored (no hard-coded keys)", ".env" in (root / ".gitignore").read_text(encoding="utf-8").split())

    # ------------------------------------------------------------------ report
    width = 74
    failed = [r for r in results if not r[2]]
    current = None
    for sec, requirement, passed, detail in results:
        if sec != current:
            print(f"\n== {sec} ==")
            current = sec
        mark = "PASS" if passed else "FAIL"
        extra = f"  ({detail})" if detail and (not passed or len(detail) < 40) else ""
        print(f"  [{mark}] {requirement[:width]}{extra}")
    print(f"\n{len(results) - len(failed)}/{len(results)} automated checks passed.")
    print("\nNot automatable - verify by phoning the number: natural tone, graceful corrections via speech,\n"
          "interruptions/barge-in, Spanish switching (if enabled), start-over, and a dropped call.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
