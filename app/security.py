"""Authentication helpers: optional API key for the REST API, shared secret for the webhook."""
from __future__ import annotations

import hmac
import logging

from fastapi import Request

from app.errors import ApiError

logger = logging.getLogger("app.security")


def _matches(provided: str | None, expected: str) -> bool:
    return bool(provided) and hmac.compare_digest(provided.encode(), expected.encode())


def require_api_key(request: Request) -> None:
    """If API_KEY is configured, /patients requests must send it as X-API-Key."""
    expected = request.app.state.settings.api_key
    if expected and not _matches(request.headers.get("x-api-key"), expected):
        raise ApiError(401, "unauthorized", "Missing or invalid API key.")


def require_webhook_secret(request: Request) -> None:
    """The voice platform proves who it is with X-Vapi-Secret (or a Bearer token)."""
    settings = request.app.state.settings
    expected = settings.vapi_webhook_secret
    if not expected:
        if settings.is_production:
            logger.error("VAPI_WEBHOOK_SECRET is not set; refusing webhook call in production")
            raise ApiError(503, "webhook_not_configured", "Webhook secret is not configured.")
        return  # local development: allow unauthenticated calls
    bearer = request.headers.get("authorization", "")
    bearer = bearer[7:] if bearer.lower().startswith("bearer ") else None
    if not (_matches(request.headers.get("x-vapi-secret"), expected) or _matches(bearer, expected)):
        raise ApiError(401, "unauthorized", "Invalid webhook secret.")
