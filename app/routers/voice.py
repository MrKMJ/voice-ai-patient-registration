"""Webhook the voice platform (Vapi) calls during and after each phone call.

Handled message types:
* ``tool-calls``         - the model invoked one of our tools; we run it and return the results.
* ``end-of-call-report`` - the call is over (normally or dropped); persist transcript/summary.
* ``status-update``      - logged only.
Anything else is acknowledged with 200 so the platform never retries needlessly.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CallLog
from app.security import require_webhook_secret
from app.voice.tools import ToolContext, run_tool

logger = logging.getLogger("app.voice")

router = APIRouter(prefix="/voice", tags=["voice"], dependencies=[Depends(require_webhook_secret)])


def _parse_tool_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the tool-call list across Vapi payload variants."""
    raw = message.get("toolCallList")
    if not raw and message.get("toolWithToolCallList"):
        raw = [item.get("toolCall", {}) for item in message["toolWithToolCallList"]]
    raw = raw or message.get("toolCalls") or []
    calls = []
    for item in raw:
        function = item.get("function") or {}
        arguments = function.get("arguments", item.get("arguments", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            {"id": item.get("id"), "name": function.get("name") or item.get("name"),
             "arguments": arguments if isinstance(arguments, dict) else {}}
        )
    return calls


@router.post("/webhook")
def webhook(request: Request, body: dict[str, Any] = Body(...), db: Session = Depends(get_db)):
    message = body.get("message", body)
    kind = message.get("type")
    call = message.get("call") or {}
    call_id = call.get("id")
    caller_number = (call.get("customer") or {}).get("number")

    if kind == "tool-calls":
        ctx = ToolContext(db=db, settings=request.app.state.settings,
                          call_id=call_id, caller_number=caller_number)
        results = []
        for tool_call in _parse_tool_calls(message):
            outcome = run_tool(ctx, tool_call["name"], tool_call["arguments"])
            logger.info("tool=%s call_id=%s ok=%s", tool_call["name"], call_id,
                        outcome.get("success", outcome.get("valid", outcome.get("found", True))))
            results.append(
                {"name": tool_call["name"], "toolCallId": tool_call["id"],
                 "result": json.dumps(outcome, default=str)}
            )
        return {"results": results}

    if kind == "end-of-call-report":
        _save_call_report(db, message, call_id, caller_number)
    elif kind == "status-update":
        logger.info("call_status call_id=%s status=%s", call_id, message.get("status"))
    return {"ok": True}


def _save_call_report(db: Session, message: dict[str, Any], call_id: str | None, caller_number: str | None) -> None:
    if not call_id:
        logger.warning("end-of-call-report without a call id; ignored")
        return
    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    try:
        log = db.scalar(select(CallLog).where(CallLog.call_id == call_id))
        if log is None:
            log = CallLog(call_id=call_id)
            db.add(log)
        log.caller_number = log.caller_number or caller_number
        log.ended_reason = message.get("endedReason")
        log.transcript = artifact.get("transcript") or message.get("transcript")
        log.summary = analysis.get("summary") or message.get("summary")
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("could not store call report call_id=%s", call_id)
        return
    outcome = "registered" if log.patient_id else "NOT registered (call ended early?)"
    logger.info("call_ended call_id=%s reason=%s patient=%s", call_id, log.ended_reason, outcome)
