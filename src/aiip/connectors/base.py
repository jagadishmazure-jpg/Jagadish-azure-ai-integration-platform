"""Connector pack contract, error taxonomy and rate-limit hints.

Error taxonomy (vendor-neutral):
    auth_expired     token rejected; adapter refreshes once, then gives up
    authz_deny       the principal is not entitled (record sharing, missing role)
    not_found        object does not exist (or is invisible)
    validation       request rejected by the vendor's field rules
    business_reject  vendor accepted the call but refused the business action (often HTTP 200)
    rate_limited     429 / quota headers; carries retry_after
    conflict         optimistic-concurrency or duplicate
    transient        5xx / connection reset; safe to retry reads
    timeout          no answer inside the tool's budget"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from aiip.shared import errors as E
from aiip.shared import http
from aiip.shared.auth import Principal

AUTH_EXPIRED = "auth_expired"
AUTHZ = "authz_deny"
NOT_FOUND = "not_found"
VALIDATION = "validation"
BUSINESS_REJECT = "business_reject"
RATE_LIMITED = "rate_limited"
CONFLICT = "conflict"
TRANSIENT = "transient"
TIMEOUT = "timeout"

ERROR_CLASSES = (
    AUTH_EXPIRED,
    AUTHZ,
    NOT_FOUND,
    VALIDATION,
    BUSINESS_REJECT,
    RATE_LIMITED,
    CONFLICT,
    TRANSIENT,
    TIMEOUT,
)

# connector error class -> gateway result_class
RESULT_CLASS = {
    AUTH_EXPIRED: E.UNAVAILABLE,
    AUTHZ: E.AUTHZ_DENY,
    NOT_FOUND: E.NOT_FOUND,
    VALIDATION: E.VALIDATION,
    BUSINESS_REJECT: E.BUSINESS_REJECT,
    RATE_LIMITED: E.RATE_LIMITED,
    CONFLICT: E.CONFLICT,
    TRANSIENT: E.UNAVAILABLE,
    TIMEOUT: E.TIMEOUT,
}


@dataclass
class RateLimitHint:
    remaining: int | None = None
    limit: int | None = None
    retry_after_s: float | None = None
    source: str = ""

    @property
    def low(self) -> bool:
        return (
            self.remaining is not None
            and self.limit is not None
            and self.remaining < max(1, self.limit // 20)
        )


class ConnectorError(Exception):
    def __init__(
        self, error_class: str, message: str, *, vendor_code: str = "", retry_after: float | None = None
    ):
        super().__init__(message)
        assert error_class in ERROR_CLASSES, error_class
        self.error_class, self.message, self.vendor_code, self.retry_after = (
            error_class,
            message,
            vendor_code,
            retry_after,
        )

    @property
    def result_class(self) -> str:
        return RESULT_CLASS[self.error_class]


@dataclass
class ConnectorContext:
    principal: Principal
    idempotency_key: str | None = None
    timeout_s: float = 5.0
    business_key: str = ""


@dataclass
class ConnectorResult:
    data: dict[str, Any]
    hint: RateLimitHint | None = None
    replayed: bool = False
    vendor_status: int = 200
    notes: list[str] = field(default_factory=list)


def status_class(status: int) -> str | None:
    if status < 400:
        return None
    return {
        401: AUTH_EXPIRED,
        403: AUTHZ,
        404: NOT_FOUND,
        409: CONFLICT,
        412: CONFLICT,
        429: RATE_LIMITED,
    }.get(status, VALIDATION if status < 500 else TRANSIENT)


def retry_after(headers: httpx.Headers) -> float | None:
    v = headers.get("retry-after")
    try:
        return float(v) if v else None
    except ValueError:
        return None


class VendorHttp:
    """HTTP to one vendor. AIIP_SAAS_<VENDOR>_URL points at a real tenant/sandbox; otherwise the
    sandbox stand-in under /<vendor> on the fake-saas service."""

    def __init__(self, vendor: str) -> None:
        self.vendor = vendor
        self.base = os.environ.get(f"AIIP_SAAS_{vendor.upper()}_URL", "")

    def client(self, timeout_s: float) -> httpx.AsyncClient:
        if self.base:  # pragma: no cover - real vendor endpoint
            return httpx.AsyncClient(base_url=self.base, timeout=timeout_s)
        return http.client("fake-saas", timeout=timeout_s)

    def path(self, p: str) -> str:
        return p if self.base else f"/{self.vendor}{p}"

    async def request(self, method: str, path: str, timeout_s: float, **kw: Any) -> httpx.Response:
        try:
            async with self.client(timeout_s) as c:
                return await c.request(method, self.path(path), **kw)
        except httpx.TimeoutException as exc:
            raise ConnectorError(TIMEOUT, f"{self.vendor} did not answer in {timeout_s}s") from exc
        except httpx.TransportError as exc:
            raise ConnectorError(TRANSIENT, f"{self.vendor} unreachable") from exc


Operation = Callable[[dict[str, Any], ConnectorContext], Awaitable[ConnectorResult]]


class ConnectorPack:
    """Subclasses register operations in `operations` and describe themselves for docs/tests."""

    name: str = ""
    vendor: str = ""
    standin: bool = True
    auth_strategy: str = ""
    user_scoped_strategy: str = ""
    idempotency_strategy: str = ""
    rate_limit_headers: tuple[str, ...] = ()
    stored_fields: dict[str, tuple[str, ...]] = {}

    def __init__(self) -> None:
        self.http = VendorHttp(self.vendor)
        self.operations: dict[str, Operation] = {}
        self.last_hint: RateLimitHint | None = None

    async def invoke(self, operation: str, args: dict[str, Any], ctx: ConnectorContext) -> ConnectorResult:
        if operation not in self.operations:
            raise ConnectorError(VALIDATION, f"{self.name} has no operation {operation}")
        result = await self.operations[operation](args, ctx)
        self.last_hint = result.hint or self.last_hint
        return result

    def hint(self, headers: httpx.Headers) -> RateLimitHint | None:
        return None

    def map_error(self, resp: httpx.Response) -> ConnectorError:
        cls = status_class(resp.status_code) or TRANSIENT
        return ConnectorError(
            cls, f"{self.vendor} returned HTTP {resp.status_code}", retry_after=retry_after(resp.headers)
        )

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vendor": self.vendor,
            "standin": self.standin,
            "operations": sorted(self.operations),
            "auth": self.auth_strategy,
            "user_scoped": self.user_scoped_strategy,
            "idempotency": self.idempotency_strategy,
            "rate_limit_headers": list(self.rate_limit_headers),
            "stored_fields": {k: list(v) for k, v in self.stored_fields.items()},
        }


async def send(
    pack: ConnectorPack, auth: Any, method: str, path: str, ctx: ConnectorContext, **kw: Any
) -> httpx.Response:
    """Authenticated vendor call with one refresh-and-retry on 401 and vendor error mapping."""
    for attempt in (1, 2):
        headers = {**kw.pop("headers", {}), **await auth.headers(ctx.principal, ctx.timeout_s)}
        resp = await pack.http.request(method, path, ctx.timeout_s, headers=headers, **kw)
        pack.last_hint = pack.hint(resp.headers)
        if resp.status_code == 401 and attempt == 1:
            auth.invalidate(ctx.principal)
            kw["headers"] = {k: v for k, v in headers.items() if k != "authorization"}
            continue
        if resp.status_code >= 400:
            raise pack.map_error(resp)
        return resp
    raise ConnectorError(AUTH_EXPIRED, f"{pack.vendor} rejected refreshed credentials")
