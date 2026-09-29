"""Sanitized errors. What reaches an agent (and therefore a model) is a stable code, a short
human message and a correlation id. Exception text and stack traces stay in the logs."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("aiip.errors")

# result_class values used in spans, audit and API errors
OK = "ok"
BUSINESS_REJECT = "business_reject"
TIMEOUT = "timeout"
AUTHZ_DENY = "authz_deny"
AUTHN_FAILED = "authn_failed"
VALIDATION = "validation_error"
RATE_LIMITED = "rate_limited"
UNAVAILABLE = "unavailable"  # circuit open or backend down
BUDGET = "budget_exceeded"
NOT_FOUND = "not_found"
CONFLICT = "conflict"
APPROVAL_REQUIRED = "approval_required"
INTERNAL = "internal_error"

HTTP_STATUS = {
    BUSINESS_REJECT: 422,
    TIMEOUT: 504,
    AUTHZ_DENY: 403,
    AUTHN_FAILED: 401,
    VALIDATION: 400,
    RATE_LIMITED: 429,
    UNAVAILABLE: 503,
    BUDGET: 429,
    NOT_FOUND: 404,
    CONFLICT: 409,
    APPROVAL_REQUIRED: 428,
    INTERNAL: 500,
}


class GatewayError(Exception):
    """Raised anywhere in a gateway; rendered as a sanitized JSON error."""

    def __init__(
        self, code: str, message: str, *, detail: dict | None = None, retry_after: float | None = None
    ):
        super().__init__(message)
        self.code, self.message, self.detail, self.retry_after = code, message, detail or {}, retry_after

    @property
    def status(self) -> int:
        return HTTP_STATUS.get(self.code, 500)


def error_body(code: str, message: str, correlation_id: str, detail: dict | None = None) -> dict:
    body = {"error": {"code": code, "message": message, "correlation_id": correlation_id}}
    if detail:
        body["error"]["detail"] = detail
    return body


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GatewayError)
    async def _gw(request: Request, exc: GatewayError):
        cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex
        headers = {"retry-after": str(int(exc.retry_after or 1))} if exc.retry_after else None
        return JSONResponse(error_body(exc.code, exc.message, cid, exc.detail), exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _val(request: Request, exc: RequestValidationError):
        cid = uuid.uuid4().hex
        fields = [".".join(str(p) for p in e.get("loc", [])) for e in exc.errors()]
        return JSONResponse(error_body(VALIDATION, "request failed validation", cid, {"fields": fields}), 400)

    @app.exception_handler(Exception)
    async def _any(request: Request, exc: Exception):
        cid = uuid.uuid4().hex
        log.exception("unhandled error correlation_id=%s", cid)  # full trace goes to logs only
        return JSONResponse(error_body(INTERNAL, "internal error", cid), 500)
