"""Create or update the Vapi assistant (and optionally a phone number) for this project.

Usage (from the repo root, with .env filled in or the variables exported):

    python scripts/create_vapi_assistant.py --print        # show the JSON, change nothing
    python scripts/create_vapi_assistant.py                # create the assistant (or update it
                                                           # if VAPI_ASSISTANT_ID is set)
    python scripts/create_vapi_assistant.py --buy-number 415   # also get a free Vapi US number
                                                               # in that area code

Required: PUBLIC_BASE_URL (your deployed https URL), VAPI_WEBHOOK_SECRET, VAPI_API_KEY.
Re-run it after editing app/voice/prompt.py or app/voice/tools.py to push the changes.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.voice.assistant import build_assistant_config  # noqa: E402

VAPI_API = "https://api.vapi.ai"


def call_vapi(method: str, path: str, api_key: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        VAPI_API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        sys.exit(f"Vapi returned HTTP {exc.code} for {method} {path}:\n{exc.read().decode()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--print", action="store_true", dest="dry_run", help="print the config and exit")
    parser.add_argument("--buy-number", metavar="AREA_CODE", help="provision a free Vapi US number")
    args = parser.parse_args()

    settings = get_settings()
    missing = [
        name for name, value in (
            ("PUBLIC_BASE_URL", settings.public_base_url),
            ("VAPI_WEBHOOK_SECRET", settings.vapi_webhook_secret),
            *([] if args.dry_run else [("VAPI_API_KEY", settings.vapi_api_key)]),
        ) if not value
    ]
    if missing:
        sys.exit(f"Set these first (in .env or the environment): {', '.join(missing)}")

    config = build_assistant_config(
        settings, public_base_url=settings.public_base_url, webhook_secret=settings.vapi_webhook_secret
    )
    if args.dry_run:
        print(json.dumps(config, indent=2))
        return

    if settings.vapi_assistant_id:
        assistant = call_vapi("PATCH", f"/assistant/{settings.vapi_assistant_id}", settings.vapi_api_key, config)
        print(f"Updated assistant {assistant['id']}")
    else:
        assistant = call_vapi("POST", "/assistant", settings.vapi_api_key, config)
        print(f"Created assistant {assistant['id']}")
        print(f"Add to your .env so future runs update it in place:\n  VAPI_ASSISTANT_ID={assistant['id']}")

    if args.buy_number:
        number = call_vapi(
            "POST", "/phone-number", settings.vapi_api_key,
            {"provider": "vapi", "numberDesiredAreaCode": args.buy_number, "assistantId": assistant["id"]},
        )
        print(f"Phone number: {number.get('number') or number.get('id')}  (assistant attached)")
    else:
        print("Attach a phone number to this assistant in the Vapi dashboard (Phone Numbers),\n"
              "or re-run with --buy-number <area code>.")


if __name__ == "__main__":
    main()
