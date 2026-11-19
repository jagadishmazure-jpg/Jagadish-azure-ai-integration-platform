# retrieval

Offline stand-ins for hybrid retrieval, a knowledge graph, permission-trimmed grounding, and a governed work-context path. No live Azure AI Search, Foundry IQ, SharePoint, or Work IQ calls.

| File | What it does |
|---|---|
| [`__init__.py`](__init__.py) | Package marker and `reset()` for tests |
| [`corpus.py`](corpus.py) | Synthetic passages, entities, edges, and the caller directory |
| [`textutil.py`](textutil.py) | Deterministic tokenizer |
| [`permissions.py`](permissions.py) | SharePoint-style trim: user, Entra group, site group; missing ACL fails closed |
| [`hybrid.py`](hybrid.py) | Azure AI Search-shaped hybrid rank: BM25, hashing-trick vectors, reciprocal rank fusion |
| [`graph.py`](graph.py) | Knowledge-graph walk, topics, and a small GQL subset. Public preview stand-in [verify] |
| [`planner.py`](planner.py) | Chooses hybrid, graph, or topics. Retriever 1 stand-in [verify] |
| [`governance.py`](governance.py) | Admin ceiling, agent policy, WorkIQ.Read / WorkIQ.Act, audit |
| [`access.py`](access.py) | The one `ground` and `act` path used by REST, MCP, and A2A |
| [`listen.py`](listen.py) | Listen trigger: a matching event starts a context-agent run |
| [`rest.py`](rest.py) | REST API, including the admin ceiling and Listen ingress |
| [`mcp_server.py`](mcp_server.py) | MCP tools over the same path. The caller is the token, not an argument |
| [`handler.py`](handler.py) | A2A skills for `context-agent` |
| [`evalcmp.py`](evalcmp.py) | Comparison eval: recall, follow-up searches, leakage |
