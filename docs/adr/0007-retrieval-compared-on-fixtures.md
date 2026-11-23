# ADR 0007: Compare hybrid retrieval and a knowledge graph on fixtures

- **Status:** Accepted

## Context

Grounding an agent on enterprise documents can be a hybrid index (keyword plus vector) or a knowledge graph that keeps the relationship between entities and the document that justified it. Permission trimming has to happen before the model sees text. A second product surface, Work IQ, exposes Microsoft 365-style context over MCP, REST, and A2A, with an admin ceiling and an event trigger named Listen.

This repository had no vector index. Reviewers still need to run the comparison without a subscription, a Foundry project, or SharePoint.

## Decision

Add both retrievers as deterministic stand-ins over one synthetic corpus, score them with an eval that fails on leakage or a recall drop, and send REST, MCP, and A2A through one govern function. The admin ceiling is the upper bound. Listen starts a context-agent run from a subscribed event. Preview behavior is marked [verify] and is not described as verified against the service. Live Azure AI Search, Foundry IQ, SharePoint, and Work IQ are not called.

## Consequences

- The comparison is repeatable in CI. It measures this code: lexical overlap versus explicit edges, not the quality of a hosted embedding model or Retriever 1.
- A passage the caller cannot read is removed before scoring.
- The context agent is in-process. It is not a new Container App, and the demo's process count is unchanged.
- The Azure and Work IQ paths stay unverified until a tenant exists. The docs say that plainly.
