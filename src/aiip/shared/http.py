"""Service-to-service HTTP. One code path for three topologies:

* `AIIP_<SERVICE>_URL` set  -> real HTTP (demo topology, Container Apps).
* not set                   -> in-process ASGI transport to the service's FastAPI app (tests).

Services are addressed by logical name, so no module builds a URL by hand."""

from __future__ import annotations

import importlib
import os
from typing import Any

import httpx

# logical service name -> "module:attribute" of its ASGI app (used only when no URL is configured)
INPROC_APPS: dict[str, str] = {
    "identity": "aiip.identity.gateway:app",
    "tool-gateway": "aiip.tools.gateway:app",
    "mcp-gateway": "aiip.mcp.gateway:app",
    "a2a-gateway": "aiip.a2a.gateway:app",
    "event-gateway": "aiip.events.gateway:app",
    "bpm-host": "aiip.bpm.host:app",
    "fake-saas": "aiip.fakesaas.app:app",
    "agent-care-planner": "aiip.agents.apps:care_planner_app",
    "agent-crm": "aiip.agents.apps:crm_app",
    "agent-erp": "aiip.agents.apps:erp_app",
    "agent-data": "aiip.agents.apps:data_app",
    "agent-ap-invoice": "aiip.agents.apps:ap_invoice_app",
}

_cache: dict[str, Any] = {}


def env_name(service: str) -> str:
    return f"AIIP_{service.upper().replace('-', '_')}_URL"


def service_url(service: str) -> str | None:
    url = os.environ.get(env_name(service))
    return url.rstrip("/") if url else None


def _inproc_app(service: str):
    if service not in _cache:
        mod, attr = INPROC_APPS[service].split(":")
        _cache[service] = getattr(importlib.import_module(mod), attr)
    return _cache[service]


def client(service: str, timeout: float = 10.0, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    url = service_url(service)
    if url:
        return httpx.AsyncClient(base_url=url, timeout=timeout, headers=headers)
    # raise_app_exceptions=False: behave like a real server (500 + sanitized body), not re-raise
    transport = httpx.ASGITransport(app=_inproc_app(service), raise_app_exceptions=False)
    return httpx.AsyncClient(
        transport=transport, base_url=f"http://{service}.inproc", timeout=timeout, headers=headers
    )
