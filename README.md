# Voice AI Patient Registration Agent

Call a phone number, talk naturally to an AI intake coordinator, and your patient record is
saved to a database and available through a REST API and a small dashboard. Call back and the
agent recognises you.

| | |
|---|---|
| **Phone number** | **+1 (213) 727-7177** |
| **API base URL** | https://api-production-5801.up.railway.app |
| **Dashboard** | https://api-production-5801.up.railway.app/dashboard |
| **Interactive API docs** | https://api-production-5801.up.railway.app/docs |
| **API access** | `/patients*` requires the header `X-API-Key: <key>` (key supplied with the submission). The dashboard asks for it once. `/health` is open; `/voice/webhook` uses its own secret. |
| **Test notes** | Demo data is fictional (HIPAA out of scope). Seed patients: Alex Rivera (212-555-0101), Priya Natarajan (415-555-0134). |

## Architecture

```
 Caller ──PSTN──▶  Vapi  (telephony + speech-to-text + LLM + text-to-speech)
                     │  tool calls / call reports  (HTTPS, X-Vapi-Secret)
                     ▼
        ┌──────────────── FastAPI service (this repo) ────────────────┐
        │  routers/voice.py      webhook: auth, parse, dispatch        │
        │  voice/tools.py        tools the LLM can call                │
        │  voice/prompt.py       system prompt (documented)            │
        │  routers/patients.py   REST API  /patients  (+ dashboard)    │
        │  schemas.py + validators.py   one set of validation rules    │
        │  services/             data layer shared by REST and voice   │
        └───────────────────────────────┬──────────────────────────────┘
                                        ▼
                      PostgreSQL (prod)  /  SQLite (local)
```

Separation of concerns:

* **Telephony / STT / TTS / turn-taking** - Vapi. Not re-implemented: the brief encourages it and
  it is where most latency and barge-in handling lives.
* **LLM conversation logic** - the prompt in [`app/voice/prompt.py`](app/voice/prompt.py) plus
  tool definitions in [`app/voice/tools.py`](app/voice/tools.py). The model manages the dialogue.
* **Data layer** - `services/` + `models.py`. The REST API and the voice tools call the *same*
  functions, so there is a single code path that writes patients.
* **Validation** - `validators.py` is the only place field rules live. Pydantic schemas (REST) and
  voice tools both use it, so the server never trusts the model: the agent re-checks values as it
  goes, and `save_patient` re-validates everything.

### What happens on a call

1. Vapi answers and Riley greets the caller; the caller's speech is transcribed and sent to the LLM.
2. The LLM collects fields conversationally. After risky fields (phone, date of birth, state,
   ZIP, email, IDs) it calls `validate_field`. Invalid values come back with a speakable message
   ("That number only had seven digits...") and the agent re-asks *only that field*.
3. Validating the phone number also checks for a returning caller (chained server-side, so the model can't skip it):
   *"It looks like we already have a record for Jane Davis. Would you like to update your information instead?"*
   Updating requires the date of birth on file (3 failed attempts lock updates for that call).
4. Required fields done -> optional offer (insurance, emergency contact, language, email) -> a
   full read-back -> explicit confirmation -> `save_patient` -> *"You're all set, Jane."* -> optional
   first appointment (mock schedule) -> goodbye -> the built-in end-call tool hangs up.
5. When the call ends, Vapi posts an `end-of-call-report`; the transcript and summary are stored in
   `call_logs`, linked to the patient, and shown on the dashboard.

### Prompt engineering

The prompt (fully commented in `app/voice/prompt.py`) is built around how phone conversations fail:

* **Voice-first style**: 1-2 short sentences, one question at a time, digits grouped, dates spoken.
* **Flexible slot filling**: the caller can volunteer anything in any order; the agent only asks for what is missing.
* **Corrections** ("it's D-A-V-I-S"), **interruptions**, **"start over"**, **Spanish** (opt-in, see below) and **wants-a-human** are each handled explicitly.
* **Never save without a read-back and a clear yes.**
* **Tool results carry an `instruction`**: edge-case behaviour (re-ask only field X, apologise and retry,
  offer an update) lives in tested server code, not just in prose the model might skip.

### Edge cases and resilience

| Situation | Behaviour |
|---|---|
| Invalid DOB / phone / state / ZIP / email | `validate_field` and `save_patient` return per-field messages; agent re-asks that field only; nothing half-saved |
| Future or impossible date of birth | Rejected server-side (also in the REST API) |
| Database write fails | Tool returns `error_type: system` + an instruction: apologise, retry once, otherwise promise a staff call-back. The caller never hears silence |
| Model retries `save_patient` | Idempotent per call: returns the existing record instead of creating a duplicate |
| Same person calls again | Phone lookup -> offer to update; DOB verification before any change |
| Shared household phone, different person | Allowed; only phone **and** date of birth identical counts as a duplicate |
| Call drops mid-registration | End-of-call report is still stored (transcript, reason), flagged "not registered" in logs; no partial record is created |
| Caller says start over | Prompt discards state and restarts; nothing was saved yet |
| Webhook secret wrong/missing | 401; in `ENVIRONMENT=production` with no secret configured the webhook refuses to run |
| A tool crashes | Caught; model gets a system-error instruction rather than the call failing |

## REST API

All responses use `{ "data": ..., "error": null, "meta": ... }`; errors use
`{ "data": null, "error": { "code", "message", "details": [{ "field", "message" }] } }`.

| Method | Path | Notes |
|---|---|---|
| GET | `/patients` | Filters: `?last_name=` (case-insensitive), `?date_of_birth=MM/DD/YYYY`, `?phone_number=`; `limit`, `offset` |
| GET | `/patients/{id}` | 200 / 400 (bad UUID) / 404 |
| POST | `/patients` | 201 with the created record; 422 field errors; 400 malformed JSON |
| PUT | `/patients/{id}` | Partial update; `null` clears an optional field; empty body is 400 |
| DELETE | `/patients/{id}` | Soft delete (`deleted_at`), row retained, hidden from reads |
| GET | `/patients/{id}/calls` | Bonus: call transcripts/summaries |
| GET | `/patients/{id}/appointments` | Bonus: booked appointments |
| GET | `/health` | DB check, for platform probes |
| POST | `/voice/webhook` | Voice platform only (secret-protected) |

```bash
curl -X POST $BASE/patients -H 'content-type: application/json' -H "X-API-Key: $API_KEY" -d '{
  "first_name":"Jane","last_name":"Doe","date_of_birth":"03/14/1985","sex":"Female",
  "phone_number":"(212) 555-0188","address_line_1":"1 Main St","city":"Brooklyn","state":"NY","zip_code":"11201"}'
curl "$BASE/patients?last_name=doe" -H "X-API-Key: $API_KEY"
```

Phone numbers are stored as 10 digits; dates are stored as `DATE` and rendered `MM/DD/YYYY`;
timestamps are UTC ISO-8601. The schema (`app/models.py`) enforces lengths, the sex enum, state/phone
length and ZIP shape with CHECK constraints so the database is a backstop to the API validation.

## Local setup

```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                   # then edit; set VAPI_WEBHOOK_SECRET
uvicorn app.main:app --reload                          # http://localhost:8000/dashboard
pytest                                                 # 54 tests, no network needed
```

To test the real phone flow locally, expose the port (`ngrok http 8000` or
`cloudflared tunnel --url http://localhost:8000`), put the https URL in `PUBLIC_BASE_URL`, and run the
Vapi script below. For a permanent deployment see **[DEPLOYMENT.md](DEPLOYMENT.md)**.

```bash
python scripts/create_vapi_assistant.py --print            # inspect what will be sent
python scripts/create_vapi_assistant.py --buy-number 415   # create assistant + free US number
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./data/patients.db` | Any SQLAlchemy URL; `postgres://` / `postgresql://` are normalised |
| `VAPI_WEBHOOK_SECRET` | _(empty)_ | Shared secret Vapi sends as `X-Vapi-Secret`. **Required in production** |
| `API_KEY` | _(empty)_ | If set, `/patients*` requires `X-API-Key` (dashboard prompts for it) |
| `ENVIRONMENT` | `development` | `production` hard-fails the webhook without a secret |
| `SEED_DEMO_DATA` | `false` | Insert two fictional patients into an empty DB |
| `LOG_LEVEL` / `LOG_PII` | `INFO` / `true` | `LOG_PII=false` masks patient data in logs |
| `CLINIC_NAME` | Riverside Family Clinic | Used in the prompt and greeting |
| `VAPI_TRANSCRIBER_LANGUAGE` | `en` | `multi` enables Spanish (script-only; may mis-hear accented English) |
| `PUBLIC_BASE_URL`, `VAPI_API_KEY`, `VAPI_ASSISTANT_ID`, `VAPI_VOICE_*`, `VAPI_LLM_*` | | Only for `scripts/create_vapi_assistant.py` |

No secrets are in the source; `.env` is git-ignored.

## Tech stack and why

* **Vapi** - fastest path to a real dialable number with STT/TTS, barge-in and tool calling handled;
  lets the effort go into prompt, tools and data integrity. Deepgram `nova-3` handles speech-to-text
  (English by default, `multi` for Spanish); GPT-4o is the default LLM and is a one-line change (`VAPI_LLM_*`).
* **FastAPI + Pydantic v2** - typed validation gives clean 422s, free OpenAPI docs at `/docs`.
* **SQLAlchemy 2 + PostgreSQL** (SQLite for local/dev) - relational constraints fit a fixed
  demographic schema; the same code runs on both.
* **Docker** - one image deploys to Railway, Render, Fly or anywhere.

## Observability

Everything logs to stdout: every tool call, the **final collected payload** on registration/update
(`patient_registered ...` / `patient_updated ...`), call-ended events including calls that finished
without a registration, and errors with stack traces. Full transcripts are in `call_logs` and on the dashboard.

## Known limitations and trade-offs

* **Not HIPAA-grade** (per the brief): no encryption at rest beyond what the host provides, no audit trail,
  PII in logs unless `LOG_PII=false`. Use fictional data.
* **Tables via `create_all`, no migrations.** Fine for a fixed schema in a take-home; Alembic is the next step.
* **No unique constraint on phone number**: households share numbers. Duplicate detection is done by the
  voice layer (phone + date of birth) rather than the database.
* **Identity check is a DOB knowledge check** - proportionate for a demo, weak for production.
* **The LLM is probabilistic.** Server-side validation, idempotent saves and tool instructions bound the
  damage, but real call QA is still needed. The webhook/tool layer is unit-tested; the live conversation is
  verified by placing calls.
* **Draft state is not persisted mid-call**, so a dropped call loses what was collected (the transcript is kept).
* **Appointments are a mock** (one provider, next three business days, clinic-local time).
* **Optional fields can't be cleared by voice** - only via the REST API.
* Free hosting tiers can sleep or expire databases; use a paid always-on instance for anything real.

## Next steps

Persist partial drafts keyed by call id and call the person back after a drop; Alembic migrations;
caller-ID shortcut ("is this the best number to reach you?"); stronger identity verification (OTP by SMS);
rate limiting; per-call QA scoring from transcripts; real scheduling integration.

## Project layout

```
app/
  main.py            app factory, health, dashboard
  config.py          settings from environment
  models.py          Patient, CallLog, Appointment (+ constraints)
  validators.py      all field rules
  schemas.py         request/response models and the envelope
  errors.py          uniform error envelope and handlers
  security.py        API key + webhook secret
  services/          patients.py, appointments.py (shared data layer)
  routers/           patients.py (REST), voice.py (webhook)
  voice/             prompt.py, tools.py, assistant.py (Vapi config)
  static/dashboard.html
scripts/             create_vapi_assistant.py, entrypoint.sh
tests/               validators, REST API, voice webhook/tool flows
```
