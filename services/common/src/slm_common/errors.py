from __future__ import annotations

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"
_PROBLEM_BASE = "https://slm-studio.dev/problems/"
_log = structlog.get_logger(__name__)

_OPENAI_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "invalid_request_error",
    409: "invalid_request_error",
    422: "invalid_request_error",
    429: "rate_limit_error",
}


class ApiError(Exception):
    """An error with an HTTP status and a stable machine-readable code."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        param: str | None = None,
        headers: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.param = param
        self.headers = headers or {}
        self.extra = extra or {}

    @classmethod
    def bad_request(cls, message: str, code: str = "bad_request", **kw: Any) -> ApiError:
        return cls(400, code, message, **kw)

    @classmethod
    def unauthorized(
        cls, message: str = "Authentication required", code: str = "unauthorized"
    ) -> ApiError:
        return cls(401, code, message, headers={"WWW-Authenticate": "Bearer"})

    @classmethod
    def forbidden(
        cls, message: str = "Insufficient permissions", code: str = "forbidden"
    ) -> ApiError:
        return cls(403, code, message)

    @classmethod
    def not_found(cls, what: str = "resource", code: str = "not_found") -> ApiError:
        return cls(404, code, f"{what} not found")

    @classmethod
    def conflict(cls, message: str, code: str = "conflict") -> ApiError:
        return cls(409, code, message)

    @classmethod
    def not_implemented(cls, what: str) -> ApiError:
        return cls(501, "not_implemented", f"{what} is not implemented yet")


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None) or request.headers.get("x-request-id")


def _normalize(exc: Exception) -> ApiError:
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, RequestValidationError):
        errors = jsonable_encoder(exc.errors(), exclude={"ctx", "input", "url"})
        first = errors[0] if errors else {}
        loc = ".".join(str(p) for p in first.get("loc", ()) if p != "body")
        message = f"{loc}: {first.get('msg')}" if first else "Invalid request"
        return ApiError(
            422, "validation_error", message, param=loc or None, extra={"errors": errors}
        )
    if isinstance(exc, StarletteHTTPException):
        code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
        return ApiError(exc.status_code, code, str(exc.detail), headers=dict(exc.headers or {}))
    _log.error("unhandled_exception", exc_info=exc)
    return ApiError(500, "internal_error", "Internal server error")


def _phrase(status: int) -> str:
    try:
        return HTTPStatus(status).phrase
    except ValueError:
        return "Error"


async def problem_json_handler(request: Request, exc: Exception) -> JSONResponse:
    """RFC 7807 response used by the control plane (`/api/v1/*`)."""
    err = _normalize(exc)
    body: dict[str, Any] = {
        "type": _PROBLEM_BASE + err.code,
        "title": _phrase(err.status_code),
        "status": err.status_code,
        "detail": err.message,
        "code": err.code,
        "instance": request.url.path,
        "request_id": _request_id(request),
        **err.extra,
    }
    if err.param:
        body["param"] = err.param
    return JSONResponse(body, err.status_code, headers=err.headers, media_type=PROBLEM_MEDIA_TYPE)


async def openai_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """OpenAI-shaped response used by the inference API (`/v1/*`)."""
    err = _normalize(exc)
    if isinstance(exc, RequestValidationError):
        err = ApiError(400, "invalid_request_body", err.message, param=err.param)
    error_type = _OPENAI_TYPES.get(
        err.status_code, "server_error" if err.status_code >= 500 else "invalid_request_error"
    )
    body = {
        "error": {
            "message": err.message,
            "type": error_type,
            "param": err.param,
            "code": err.code,
        }
    }
    return JSONResponse(body, err.status_code, headers=err.headers)


def install_exception_handlers(app: FastAPI, style: str = "problem") -> None:
    handler = openai_error_handler if style == "openai" else problem_json_handler
    for exc_type in (ApiError, RequestValidationError, StarletteHTTPException, Exception):
        app.add_exception_handler(exc_type, handler)
