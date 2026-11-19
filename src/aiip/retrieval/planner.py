"""Retrieval planner.

A stand-in for Microsoft Retriever 1, which plans each search and is in public preview [verify].
The rules below are deterministic and do not call that model. A relational or regulation question
goes to the graph. A theme question goes to topics. Anything else is a single hybrid query.
"""

from __future__ import annotations

RETRIEVER_NOTE = "Microsoft Retriever 1 public preview [verify]"
GRAPH_NOTE = "Foundry IQ knowledge graph public preview [verify]"

_BROAD = ("which themes", "across the corpus", "which topics", "what topics")
_GRAPH = ("regulation", "eu-coating-2026", "which agreement", "compound", "supplier")


def plan(question: str) -> tuple[str, str]:
    """Return ``(mode, note)`` where mode is hybrid, graph or topics."""
    q = question.lower()
    if any(phrase in q for phrase in _BROAD):
        return "topics", GRAPH_NOTE
    if any(phrase in q for phrase in _GRAPH):
        return "graph", f"{RETRIEVER_NOTE}; {GRAPH_NOTE}"
    return "hybrid", ""
