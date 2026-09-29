"""OpenTelemetry for integration work.

Every call that crosses the integration plane is wrapped in `integration_span`, which stamps the
fields an operator needs to answer "did the business process complete?":

    integration.system        salesforce | sap | servicenow | workday | dataverse | jira | mcp:<server> | a2a:<agent>
    integration.operation     tool / skill / event name
    integration.business_key  order number, invoice number, account id ...
    integration.result_class  ok | timeout | business_reject | authz_deny | (validation_error, unavailable, ...)
    integration.identity_mode obo | agent
    integration.actor / integration.subject / tenant.id

A 200 whose payload carries a functional error is recorded as `business_reject`, never `ok`.
Exporters: Azure Monitor when APPLICATIONINSIGHTS_CONNECTION_STRING is set; an in-memory exporter
otherwise (tests and the demo read from it)."""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from opentelemetry import metrics, propagate, trace
from opentelemetry.metrics import CallbackOptions, Observation

from aiip.shared import errors as E

_lock = threading.Lock()
_configured: str | None = None
_span_exporter = None
_metric_reader = None


def configure(service_name: str = "aiip") -> str:
    """Idempotent. Returns the backend in use: azure-monitor | memory."""
    global _configured, _span_exporter, _metric_reader
    with _lock:
        if _configured:
            return _configured
        os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
        import logging

        for noisy in ("httpx", "mcp", "a2a", "agent_framework"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        conn = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
        if conn:
            from azure.monitor.opentelemetry import configure_azure_monitor

            configure_azure_monitor(connection_string=conn)
            _configured = "azure-monitor"
            return _configured
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import InMemoryMetricReader
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        resource = Resource.create({"service.name": os.environ["OTEL_SERVICE_NAME"]})
        provider = TracerProvider(resource=resource)
        _span_exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_span_exporter))
        trace.set_tracer_provider(provider)
        _metric_reader = InMemoryMetricReader()
        metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[_metric_reader]))
        _configured = "memory"
        _init_instruments()
        return _configured


def finished_spans() -> list:
    configure()
    return list(_span_exporter.get_finished_spans()) if _span_exporter else []


def metric_data():
    configure()
    return _metric_reader.get_metrics_data() if _metric_reader else None


def clear_spans() -> None:
    if _span_exporter:
        _span_exporter.clear()


# ---------------------------------------------------------------- instruments
_instruments: dict[str, Any] = {}
_queue_depth_sources: dict[str, Callable[[], dict[str, int]]] = {}


def _queue_depth_cb(options: CallbackOptions) -> Iterator[Observation]:
    for source in list(_queue_depth_sources.values()):
        for queue, depth in source().items():
            yield Observation(depth, {"queue": queue})


def _init_instruments() -> None:
    meter = metrics.get_meter("aiip")
    _instruments["calls"] = meter.create_counter(
        "integration.calls", description="Integration calls by result"
    )
    _instruments["duration"] = meter.create_histogram(
        "integration.duration", unit="ms", description="Integration call latency"
    )
    _instruments["process"] = meter.create_counter(
        "process.completions", description="Business process outcomes (completed, rejected, compensated...)"
    )
    _instruments["identity"] = meter.create_counter(
        "integration.identity_mode", description="Calls by principal type (obo vs agent)"
    )
    meter.create_observable_gauge(
        "queue.depth", callbacks=[_queue_depth_cb], description="Queue depth incl. DLQ"
    )


def register_queue_depth(name: str, source: Callable[[], dict[str, int]]) -> None:
    configure()
    _queue_depth_sources[name] = source


def record_process(process: str, outcome: str, **attrs: Any) -> None:
    configure()
    if "process" in _instruments:
        _instruments["process"].add(1, {"process": process, "outcome": outcome, **attrs})
    LEDGER.process(process, outcome)


# ---------------------------------------------------------------- ledger (for demo + /v1/metrics)
@dataclass
class CallRecord:
    service: str
    system: str
    operation: str
    result_class: str
    duration_ms: float
    identity_mode: str
    business_key: str


@dataclass
class Ledger:
    """Bounded record of real calls made by this process. Backs `GET /v1/metrics` and the demo summary;
    the numbers are measured, never configured."""

    calls: deque = field(default_factory=lambda: deque(maxlen=5000))
    processes: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, rec: CallRecord) -> None:
        self.calls.append(rec)

    def process(self, process: str, outcome: str) -> None:
        self.processes.setdefault(process, {}).setdefault(outcome, 0)
        self.processes[process][outcome] += 1

    def summary(self, service: str | None = None) -> dict[str, Any]:
        rows = [c for c in self.calls if service is None or c.service == service]
        by_system: dict[str, dict[str, int]] = {}
        latencies: dict[str, list[float]] = {}
        identity: dict[str, int] = {}
        for c in rows:
            by_system.setdefault(c.system, {}).setdefault(c.result_class, 0)
            by_system[c.system][c.result_class] += 1
            latencies.setdefault(c.operation, []).append(c.duration_ms)
            if c.identity_mode:
                identity[c.identity_mode] = identity.get(c.identity_mode, 0) + 1
        return {
            "calls": len(rows),
            "success_by_system": {
                s: {"ok": v.get("ok", 0), "total": sum(v.values()), "by_result": v}
                for s, v in by_system.items()
            },
            "p95_ms_by_operation": {op: round(p95(v), 2) for op, v in latencies.items()},
            "identity_mix": identity,
            "processes": self.processes,
        }

    def reset(self) -> None:
        self.calls.clear()
        self.processes.clear()


def p95(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, round(0.95 * (len(s) - 1))))
    return s[idx]


LEDGER = Ledger()


# ---------------------------------------------------------------- span helper
class IntegrationSpan:
    def __init__(self, span: Any) -> None:
        self.span = span
        self.result_class = E.OK

    def set_result(self, result_class: str) -> None:
        self.result_class = result_class

    def set(self, key: str, value: Any) -> None:
        if value is not None:
            self.span.set_attribute(key, value if isinstance(value, str | int | float | bool) else str(value))


@contextmanager
def integration_span(
    name: str,
    *,
    service: str,
    system: str,
    operation: str,
    business_key: str | None = None,
    identity_mode: str | None = None,
    actor: str | None = None,
    subject: str | None = None,
    tenant: str | None = None,
    traceparent: str | None = None,
    **extra: Any,
) -> Iterator[IntegrationSpan]:
    configure(service)
    tracer = trace.get_tracer("aiip")
    ctx = propagate.extract({"traceparent": traceparent}) if traceparent else None
    started = time.perf_counter()
    with tracer.start_as_current_span(name, context=ctx) as span:
        h = IntegrationSpan(span)
        for k, v in {
            "integration.system": system,
            "integration.operation": operation,
            "integration.business_key": business_key,
            "integration.identity_mode": identity_mode,
            "integration.actor": actor,
            "integration.subject": subject,
            "tenant.id": tenant,
            "service.component": service,
        }.items():
            h.set(k, v)
        for k, v in extra.items():
            h.set(f"integration.{k}", v)
        try:
            yield h
        except E.GatewayError as exc:
            if h.result_class == E.OK:
                h.result_class = exc.code
            raise
        except Exception:
            if h.result_class == E.OK:
                h.result_class = E.INTERNAL
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            span.set_attribute("integration.result_class", h.result_class)
            if h.result_class != E.OK:
                from opentelemetry.trace import Status, StatusCode

                span.set_status(Status(StatusCode.ERROR, h.result_class))
            attrs = {"system": system, "operation": operation, "result_class": h.result_class}
            if "calls" in _instruments:
                _instruments["calls"].add(1, attrs)
                _instruments["duration"].record(elapsed, {"system": system, "operation": operation})
                if identity_mode:
                    _instruments["identity"].add(1, {"identity_mode": identity_mode})
            LEDGER.add(
                CallRecord(
                    service,
                    system,
                    operation,
                    h.result_class,
                    elapsed,
                    identity_mode or "",
                    business_key or "",
                )
            )


def current_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier.get("traceparent")
