# ADR 0006: Five gateway services, not a shared library

- **Status:** Accepted
- **Date:** 2026-09-29

## Context

Agents need to call SAP, Salesforce, ServiceNow, Workday, Dynamics and Jira. A shared client library would put credentials, retry and policy code inside every agent, where it can be bypassed or drift between versions.

## Decision

Agents hold no vendor connections. All calls go through five services (tool, MCP, A2A, event and identity gateways) that own identity exchange, allow-lists, idempotency, rate limits, circuit breakers, audit and the kill switch.

## Consequences

- One place to review and test policy per system, and agents have nothing to bypass it with.
- An extra network hop per call, measured in the demo.
- The gateways become critical services: they need their own scaling, monitoring and on-call ownership.
