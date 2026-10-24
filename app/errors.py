"""One error envelope for every failure: {"data": null, "error": {code, message, details}}."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.validators import FIELD_LABELS

logger = logging.getLogger("app.errors")


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, str]] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


def error_response(
    status_code: int, code: str, message: str, details: list[dict[str, str]] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "data": None,
            "error": {"code": code, "message": message, "details": details or []},
        },
    )


def format_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Turn pydantic error dicts into [{field, message}] with speakable messages."""
    details = []
    for err in errors:
        field = ".".join(str(p) for p in err["loc"] if p not in ("body", "query"))
        label = FIELD_LABELS.get(field, field or "request")
        kind = err.get("type", "")
        if kind == "missing":
            message = f"The {label} is required."
        elif kind == "extra_forbidden":
            message = "Unknown field."
        elif "error" in (err.get("ctx") or {}):
            message = str(err["ctx"]["error"])  # the ValueError raised by our validators
        else:
            message = err.get("msg", "Invalid value.")
        details.append({"field": field, "message": message})
    return details


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def _request_validation(_: Request, exc: RequestValidationError):
        errors = exc.errors()
        if any(e.get("type") == "json_invalid" for e in errors):
            return error_response(400, "bad_request", "Request body is not valid JSON.")
        return error_response(
            422, "validation_error", "One or more fields are invalid.",
            format_validation_errors(errors),
        )

    @app.exception_handler(ValidationError)
    async def _model_validation(_: Request, exc: ValidationError):
        return error_response(
            422, "validation_error", "One or more fields are invalid.",
            format_validation_errors(exc.errors()),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        codes = {401: "unauthorized", 404: "not_found", 405: "method_not_allowed"}
        return error_response(
            exc.status_code, codes.get(exc.status_code, "http_error"), str(exc.detail)
        )

    @app.exception_handler(SQLAlchemyError)
    async def _db_error(_: Request, exc: SQLAlchemyError):
        logger.exception("database error")
        return error_response(500, "database_error", "A database error occurred.")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logger.exception("unhandled error")
        return error_response(500, "internal_error", "An unexpected error occurred.")
