"""Offline retrieval and grounding stand-ins.

Azure AI Search hybrid retrieval, a Foundry IQ-style knowledge graph, permission-trimmed
grounding, and a governed work-context path (MCP, REST, A2A) with a Listen trigger. Nothing
here calls a live search service or a model. Preview product behavior is marked ``[verify]``.
"""

from __future__ import annotations


def reset() -> None:
    """Restore ceilings, policies, items, audit and Listen runs. Tests call this between cases."""
    from aiip.retrieval.governance import reset_state
    from aiip.retrieval.listen import reset_runs

    reset_state()
    reset_runs()
