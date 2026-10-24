# Deploying and going live

Four steps: **host the API -> create the Vapi assistant -> get a phone number -> test calls.**
You need accounts for a host (Railway / Render / Fly.io) and [Vapi](https://vapi.ai). Keep every key in
environment variables - never commit them.

## 0. Generate the shared secret

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Use the output as `VAPI_WEBHOOK_SECRET` both on the host and in your local `.env`.

## 1. Host the API

The `Dockerfile` works everywhere. The service must be **always on** (a phone call can't wait for a cold start) and needs a **database that survives restarts**.

### Option A - Railway (recommended)

```bash
npm i -g @railway/cli && railway login
railway init                       # new project
railway add --database postgres    # managed Postgres
railway up                         # builds the Dockerfile (railway.json sets the health check)
```

In the service's **Variables** tab set:

| Variable | Value |
|---|---|
| `ENVIRONMENT` | `production` |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` (reference to the Postgres plugin) |
| `VAPI_WEBHOOK_SECRET` | the secret from step 0 |
| `SEED_DEMO_DATA` | `true` (optional) |

Then **Settings -> Networking -> Generate Domain** and note the `https://...up.railway.app` URL.

### Option B - Render

Dashboard -> **New -> Blueprint** -> select this repo (`render.yaml` creates the web service and a Postgres DB). Copy the generated `VAPI_WEBHOOK_SECRET` from the service's environment. Free Postgres expires after 30 days.

### Option C - Fly.io (SQLite on a volume)

```bash
fly launch --no-deploy --copy-config      # pick a unique app name
fly volumes create patient_data --size 1
fly secrets set VAPI_WEBHOOK_SECRET=<secret>
fly deploy
```

### Verify the host

```bash
curl https://<your-url>/health      # {"data":{"status":"ok"},"error":null,"meta":null}
```

Open `https://<your-url>/dashboard` - with `SEED_DEMO_DATA=true` you should see two patients.

## 2. Create the voice assistant

Create a Vapi API key (dashboard -> API Keys, the **private** key). Put these in your local `.env`:

```
PUBLIC_BASE_URL=https://<your-url>
VAPI_WEBHOOK_SECRET=<secret from step 0>
VAPI_API_KEY=<your private Vapi key>
```

```bash
python scripts/create_vapi_assistant.py --print    # optional: inspect the exact JSON
python scripts/create_vapi_assistant.py            # creates the assistant
```

Copy the printed `VAPI_ASSISTANT_ID=` into `.env`; later runs then **update** the same assistant after
you edit `app/voice/prompt.py` or `app/voice/tools.py`.

The model/voice are configurable through `VAPI_LLM_PROVIDER`, `VAPI_LLM_MODEL`, `VAPI_VOICE_PROVIDER`, `VAPI_VOICE_ID`.
If Vapi rejects a combination it prints the full error body so you can adjust.

## 3. Get a phone number

Easiest: let the script buy a free Vapi US number and attach the assistant:

```bash
python scripts/create_vapi_assistant.py --buy-number 415
```

Or in the Vapi dashboard: **Phone Numbers -> Create** (free Vapi number, or import a Twilio/Vonage number) and set
its assistant to "Patient Registration". If Vapi's free numbers aren't available to your account, import a
Twilio number instead; if nothing can be provisioned, document that in the README (the brief accepts it) and
demo through the Vapi dashboard's "Talk to assistant" button.

## 4. Test it

1. Call the number. Register a patient: say a wrong phone number once, give a future birth date once, correct a name by
   spelling it, then confirm.
2. `GET https://<your-url>/patients` (or the dashboard) shows the record, with the call transcript attached shortly after you hang up.
3. Call again from the same number and give the same phone number: the agent offers to update instead.
4. Watch logs live: `railway logs` / `fly logs` / Render's log tab. You'll see each `tool=...` call and the
   `patient_registered payload=...` line.

If a call goes wrong, check in order: host logs (is `/voice/webhook` returning 401? -> secret mismatch), Vapi dashboard -> Calls ->
the call's logs, then re-run the script after changing the prompt.

## Final hand-in checklist

- [ ] Fill in the phone number and base URL at the top of `README.md`
- [ ] `SEED_DEMO_DATA=true` if you want demo records; otherwise leave the DB empty
- [ ] Decide whether to set `API_KEY` (then share it with the reviewer with the submission)
- [ ] Place at least one end-to-end test call after the final deploy
- [ ] Push the repo and send: repo URL, phone number, API base URL, notes
