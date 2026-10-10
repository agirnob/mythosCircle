"""Error envelope ``{code, message, details?}`` and FastAPI handlers.

Conventions (conventions.md, AD-17):
- 4xx = user error (never a state change); 5xx = server error.
- ``code`` is machine-readable, ``message`` human-readable, ``details`` optional.
- 5xx responses are generic: no stack traces, no SQL, no internal paths.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

#: Machine-readable codes for the common 4xx statuses.
_HTTP_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    418: "im_a_teapot",
    422: "validation_error",
    429: "rate_limited",
    451: "unavailable_for_legal_reasons",
}

#: Default human-readable messages when the exception carries none.
_DEFAULT_MESSAGES: dict[int, str] = {
    400: "Bad request.",
    401: "Authentication required.",
    403: "Access denied.",
    404: "The requested resource was not found.",
    405: "Method not allowed.",
    408: "The request timed out.",
    409: "Conflict with current state.",
    418: "I'm a teapot.",
    422: "Request validation failed.",
    429: "Too many requests.",
    451: "Unavailable for legal reasons.",
}

INTERNAL_ERROR_CODE = "internal_error"
VALIDATION_ERROR_CODE = "validation_error"
INTERNAL_ERROR_MESSAGE = "Internal server error."


class StoreHTTPException(StarletteHTTPException):
    """``HTTPException`` that additionally carries envelope ``details``
    (spec-2.5: the LiveEdgesError affected-entities listing). Defined
    here so the handler can gate the ``details`` passthrough on it
    without a core→api import; re-exported from ``app.api.common``."""

    def __init__(
        self, status_code: int, detail: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.details = details


class ErrorEnvelope(BaseModel):
    """The error envelope for every non-2xx JSON response."""

    code: str  # machine-readable, e.g. "not_found"
    message: str  # human-readable
    details: dict[str, Any] | None = None


def _envelope(
    status_code: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = ErrorEnvelope(code=code, message=message, details=details)
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422: request failed validation (user error, no state change)."""
    details = {"errors": jsonable_encoder(exc.errors())}
    return _envelope(422, VALIDATION_ERROR_CODE, "Request validation failed.", details)


def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """4xx: user error in the envelope; 5xx: generic, nothing internal leaked."""
    status_code = exc.status_code
    if status_code >= 500:
        return _envelope(500, INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE)
    detail = exc.detail if isinstance(exc.detail, str) else None
    code = _HTTP_CODES.get(status_code, "error")
    message = detail if detail else _DEFAULT_MESSAGES.get(status_code, "Request failed.")
    # Only a StoreHTTPException carries envelope ``details`` (e.g.
    # spec-2.5's LiveEdgesError affected-entities listing) — a bare
    # StarletteHTTPException's attributes are never leaked to the wire.
    details = exc.details if isinstance(exc, StoreHTTPException) else None
    return _envelope(status_code, code, message, details)


def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all: 500 in the envelope. Never echoes the exception back —
    but it IS logged with its traceback (spec-1.7: production 500s must
    be visible in the JSON-lines log, not swallowed)."""
    import logging

    # ``exc_info=<the exception>`` (accepted since Python 3.5) makes the
    # formatter render its real traceback — this handler runs outside the
    # original except block, so ``sys.exc_info()`` would be empty.
    logging.getLogger(__name__).error(
        "unhandled error on %s %s",
        request.method,
        request.url.path,
        exc_info=exc,
    )
    response = _envelope(500, INTERNAL_ERROR_CODE, INTERNAL_ERROR_MESSAGE)
    if request.url.path.startswith("/api/admin/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def register_error_handlers(application: FastAPI) -> None:
    """Attach the convention error handlers to the app.

    Starlette's declared handler type is contravariant in the exception type,
    so the narrow per-type signatures need a targeted ignore at registration.
    """
    application.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    application.add_exception_handler(StarletteHTTPException, handle_http_error)  # type: ignore[arg-type]
    application.add_exception_handler(Exception, handle_unexpected_error)
