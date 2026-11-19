"""Foundry IQ-style knowledge graph, offline.

Entities and edges are mapped from the synthetic corpus with a fixed ontology. Every edge keeps
the document ids that justify it, and an edge is visible only when the caller can read every
cited document. Natural language walks two hops. A small GQL subset answers a direct pattern.
Topics are the corpus themes, not a model clustering the embeddings: automatic topic discovery
is public preview [verify]. The graph itself is public preview [verify]. This module does not
call Foundry IQ.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from aiip.retrieval.corpus import EDGES, ENTITIES, PASSAGES, Caller, Edge, Entity
from aiip.retrieval.permissions import visible_passages
from aiip.shared import errors as E

_NODE = re.compile(r"\((\w+):(\w+)\)")
_EDGE = re.compile(r"(<-|-)\s*\[:(\w+)\]\s*(->|-)")


@dataclass(frozen=True)
class GraphHit:
    doc_id: str
    title: str
    text: str
    citation: str
    path: tuple[str, ...]

    def public(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "citation": self.citation,
            "path": list(self.path),
        }


def _visible_ids(caller: Caller | None) -> set[str]:
    return {p.doc_id for p in visible_passages(caller)}


def visible_edges(caller: Caller | None) -> list[Edge]:
    allowed_docs = _visible_ids(caller)
    return [e for e in EDGES if set(e.sources) <= allowed_docs]


def _entities() -> dict[str, Entity]:
    return {e.id: e for e in ENTITIES}


def _adjacency(edges: list[Edge]) -> dict[str, list[tuple[str, str, Edge]]]:
    """node -> (neighbor, direction relative to the node, edge). direction is out or in."""
    adj: dict[str, list[tuple[str, str, Edge]]] = defaultdict(list)
    for edge in edges:
        adj[edge.source].append((edge.target, "out", edge))
        adj[edge.target].append((edge.source, "in", edge))
    for node in adj:
        adj[node].sort(key=lambda item: (item[0], item[1], item[2].type))
    return adj


def _seeds(question: str, edges: list[Edge]) -> list[str]:
    known = _entities()
    ids = {e.source for e in edges} | {e.target for e in edges}
    q = question.lower()
    found = [eid for eid in sorted(ids) if eid in known and known[eid].name.lower() in q]
    return found


def answer(question: str, caller: Caller | None) -> list[GraphHit]:
    """Two-hop walk from entities named in the question, over the caller's visible edges."""
    edges = visible_edges(caller)
    seeds = _seeds(question, edges)
    if not seeds:
        return []
    adj = _adjacency(edges)
    docs = {p.doc_id: p for p in PASSAGES}
    seen_edges: list[Edge] = []
    seen_nodes: list[str] = []
    frontier = list(seeds)
    visited = set(seeds)
    for _depth in range(2):
        nxt: list[str] = []
        for node in frontier:
            if node not in seen_nodes:
                seen_nodes.append(node)
            for neighbor, _direction, edge in adj.get(node, []):
                if edge not in seen_edges:
                    seen_edges.append(edge)
                if neighbor not in visited:
                    visited.add(neighbor)
                    nxt.append(neighbor)
        frontier = nxt
    for node in frontier:
        if node not in seen_nodes:
            seen_nodes.append(node)
    doc_ids: list[str] = []
    for edge in seen_edges:
        for doc_id in edge.sources:
            if doc_id not in doc_ids:
                doc_ids.append(doc_id)
    path = tuple(seen_nodes)
    hits = []
    for doc_id in sorted(doc_ids):
        passage = docs[doc_id]
        hits.append(GraphHit(doc_id, passage.title, passage.text, f"corpus://{doc_id}", path))
    return hits


def topics(caller: Caller | None) -> list[dict[str, object]]:
    """Themes over passages the caller can read. Hidden passages do not contribute a theme."""
    grouped: dict[str, list[str]] = defaultdict(list)
    for passage in visible_passages(caller):
        grouped[passage.topic].append(passage.doc_id)
    return [{"topic": topic, "doc_ids": grouped[topic]} for topic in sorted(grouped)]


def _parse_path(path: str) -> list[tuple[str, str] | tuple[str, str, str]]:
    """Alternating nodes ``(var, label)`` and edges ``(direction, type)`` where direction is out or in."""
    pieces: list[tuple[str, str] | tuple[str, str, str]] = []
    cursor = 0
    expect_node = True
    while cursor < len(path):
        if expect_node:
            match = _NODE.match(path, cursor)
            if not match:
                raise E.GatewayError(E.VALIDATION, "GQL path is not a node/edge chain")
            pieces.append((match.group(1), match.group(2)))
            cursor = match.end()
            expect_node = False
        else:
            match = _EDGE.match(path, cursor)
            if not match:
                break
            left, etype, right = match.group(1), match.group(2), match.group(3)
            if left == "-" and right == "->":
                direction = "out"
            elif left == "<-" and right == "-":
                direction = "in"
            else:
                raise E.GatewayError(E.VALIDATION, "GQL only allows -> and <- edges")
            pieces.append((direction, etype, "edge"))
            cursor = match.end()
            expect_node = True
        while cursor < len(path) and path[cursor].isspace():
            cursor += 1
    if expect_node or len(pieces) < 3:
        raise E.GatewayError(E.VALIDATION, "GQL path needs at least two nodes")
    return pieces


def _ident(value: str) -> bool:
    return (
        bool(value)
        and (value[0].isalpha() or value[0] == "_")
        and all(c.isalnum() or c == "_" for c in value)
    )


def _name_ref(part: str) -> bool:
    var, dot, attr = part.partition(".")
    return dot == "." and attr == "name" and _ident(var)


def _parse_statement(text: str) -> tuple[str, str, str, list[str]]:
    """Split one MATCH ... WHERE var.name = '...' RETURN var.name statement. No regex over the input."""
    lowered = text.lower()
    where_at = lowered.find(" where ")
    ret_at = lowered.find(" return ")
    if not lowered.startswith("match ") or where_at < 0 or ret_at < where_at:
        raise E.GatewayError(E.VALIDATION, "GQL statement is not in the supported subset")
    path = text[len("match ") : where_at].strip()
    where = text[where_at + len(" where ") : ret_at].strip()
    ret = text[ret_at + len(" return ") :].strip()
    var, dot, rest = where.partition(".name")
    rest = rest.strip()
    if not _ident(var.strip()) or dot != ".name" or not rest.startswith("="):
        raise E.GatewayError(E.VALIDATION, "GQL WHERE only supports var.name = '...'")
    literal = rest[1:].strip()
    if len(literal) < 3 or literal[0] != "'" or literal[-1] != "'" or "'" in literal[1:-1]:
        raise E.GatewayError(E.VALIDATION, "GQL WHERE only supports var.name = '...'")
    name = literal[1:-1]
    if not name or len(name) > 80:
        raise E.GatewayError(E.VALIDATION, "GQL WHERE only supports var.name = '...'")
    returns = [part.strip() for part in ret.split(",") if part.strip()]
    if not returns or any(not _name_ref(part) for part in returns):
        raise E.GatewayError(E.VALIDATION, "GQL RETURN only supports var.name")
    return path, var.strip(), name, returns


def query_gql(statement: str, caller: Caller | None) -> list[dict[str, str]]:
    """Run one MATCH ... WHERE var.name = '...' RETURN var.name query. No general GQL."""
    text = " ".join(statement.split())
    if len(text) > 500 or any(word in text.lower() for word in ("delete", "create", "set ", "call ", ";")):
        raise E.GatewayError(E.VALIDATION, "GQL statement is not in the supported subset")
    path, where_var, where_name, returns = _parse_statement(text)
    pieces = _parse_path(path)
    edges = visible_edges(caller)
    adj = _adjacency(edges)
    known = _entities()
    nodes = [(eid, ent) for eid, ent in known.items() if ent.name == where_name and ent.label == pieces[0][1]]
    # pieces[0] is (var, label)
    start_var = pieces[0][0]
    if where_var != start_var and where_var not in {p[0] for p in pieces if len(p) == 2}:
        raise E.GatewayError(E.VALIDATION, "WHERE variable is not in the MATCH path")
    bindings: list[dict[str, str]] = []
    for eid, _ent in nodes:
        if where_var == start_var:
            bindings.append({start_var: eid})
    # Walk hops. If the WHERE var is not the start, filter after the walk.
    if where_var != start_var:
        bindings = [{start_var: eid} for eid, ent in known.items() if ent.label == pieces[0][1]]
    hop_at = 1
    while hop_at < len(pieces):
        direction, etype, _ = pieces[hop_at]  # type: ignore[misc]
        var, label = pieces[hop_at + 1]  # type: ignore[misc]
        prev_var = pieces[hop_at - 1][0]
        nxt: list[dict[str, str]] = []
        for binding in bindings:
            current = binding[str(prev_var)]
            for neighbor, edge_dir, edge in adj.get(current, []):
                if edge_dir != direction or edge.type != etype:
                    continue
                ent = known.get(neighbor)
                if ent is None or ent.label != label:
                    continue
                step = dict(binding)
                step[str(var)] = neighbor
                nxt.append(step)
        bindings = nxt
        hop_at += 2
    rows = []
    for binding in bindings:
        ent = known[binding[where_var]]
        if ent.name != where_name:
            continue
        row = {}
        for part in returns:
            var = part.split(".", 1)[0]
            row[part] = known[binding[var]].name
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(row[k] for k in sorted(row)))
