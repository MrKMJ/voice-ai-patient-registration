"""Builds the Vapi assistant definition (model, voice, transcriber, tools, webhook).

Kept separate from the HTTP layer so it can be unit-tested and so
``scripts/create_vapi_assistant.py`` can push exactly what the server expects.
"""
from __future__ import annotations

from typing import Any

from app.config import Settings
from app.voice.prompt import build_first_message, build_system_prompt
from app.voice.tools import TOOL_SPECS

WEBHOOK_PATH = "/voice/webhook"


def build_assistant_config(settings: Settings, *, public_base_url: str, webhook_secret: str) -> dict[str, Any]:
    webhook_url = public_base_url.rstrip("/") + WEBHOOK_PATH
    server = {
        "url": webhook_url,
        "headers": {"X-Vapi-Secret": webhook_secret},
        "timeoutSeconds": 20,
    }
    tools: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters,
            },
            "server": server,
        }
        for spec in TOOL_SPECS.values()
    ]
    tools.append({"type": "endCall"})  # lets the model hang up after the goodbye

    return {
        "name": f"{settings.clinic_name} - Patient Registration",
        "firstMessage": build_first_message(settings.clinic_name),
        "firstMessageMode": "assistant-speaks-first",
        "model": {
            "provider": settings.vapi_llm_provider,
            "model": settings.vapi_llm_model,
            "temperature": 0.4,  # low: reliable field capture, still conversational
            "messages": [{"role": "system", "content": build_system_prompt(settings.clinic_name)}],
            "tools": tools,
        },
        "voice": {"provider": settings.vapi_voice_provider, "voiceId": settings.vapi_voice_id},
        # "multi" lets Deepgram handle English and Spanish in the same call.
        "transcriber": {"provider": "deepgram", "model": "nova-3", "language": "multi"},
        "server": server,
        "serverMessages": ["tool-calls", "end-of-call-report", "status-update"],
        "silenceTimeoutSeconds": 30,
        "maxDurationSeconds": 900,
        "endCallMessage": "Thanks for calling. Goodbye!",
    }
