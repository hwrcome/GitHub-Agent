from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "unknown")


def _envelope(request: Request, code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}, "request_id": _request_id(request)}


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_envelope(request, exc.code, exc.message),
        headers=exc.headers,
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_envelope(request, "VALIDATION_ERROR", "Request validation failed"),
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=_envelope(request, "INTERNAL_ERROR", "Internal server error"),
    )
