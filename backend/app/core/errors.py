import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.modules.model_inventory.domain.errors import DomainError

logger = logging.getLogger("auditra.errors")


def error_envelope(
    code: str,
    message: str,
    details: object,
    request_id: str | None,
    http_status: int,
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
            "request_id": request_id,
            "http_status": http_status,
        }
    }


def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=exc.http_status,
        content=error_envelope(exc.code, exc.message, {}, request_id, exc.http_status),
    )


def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    details = [
        {
            "loc": list(error.get("loc", [])),
            "msg": error.get("msg", ""),
            "type": error.get("type", ""),
        }
        for error in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=error_envelope(
            "VALIDATION_ERROR", "Request validation failed", details, request_id, 422
        ),
    )


def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled error: %s", exc)
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=500,
        content=error_envelope("INTERNAL_ERROR", "Internal server error", {}, request_id, 500),
    )
