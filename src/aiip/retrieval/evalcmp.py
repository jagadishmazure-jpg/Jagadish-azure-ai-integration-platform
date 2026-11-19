"""Compare hybrid retrieval with the knowledge graph on the synthetic corpus.

Scores measure this orchestration: single-shot recall, a hybrid follow-up loop, and whether any
forbidden string leaked. They are not live Azure AI Search or Foundry IQ quality numbers.
"""

from __future__ import annotations

import json
from pathlib import Path

from aiip.retrieval import graph, hybrid
from aiip.retrieval.corpus import DIRECTORY, ENTITY_NAMES, Caller
from aiip.retrieval.planner import plan

CASES = Path(__file__).resolve().parents[3] / "evals" / "retrieval-cases.jsonl"
SECRETS = ("LARKSPUR-77", "IBAN-STANDBY-4401", "ORPHAN-DOC-991")


def _ids(hits) -> list[str]:
    return [h.doc_id for h in hits]


def hybrid_loop(question: str, caller: Caller, limit: int = 4) -> tuple[list[str], int]:
    """What a hybrid-only agent does: read entity names out of each hit and search again."""
    found: list[str] = []
    seen = {question}
    queue = [question]
    executed = 0
    while queue and executed < limit:
        current = queue.pop(0)
        executed += 1
        for hit in hybrid.search(current, caller, k=4):
            if hit.doc_id not in found:
                found.append(hit.doc_id)
            for name in ENTITY_NAMES:
                fresh = (
                    name.lower() in hit.text.lower()
                    and name.lower() not in current.lower()
                    and name not in seen
                )
                if fresh and len(seen) < limit:
                    seen.add(name)
                    queue.append(name)
    return found, executed


def _recall(found: list[str], supporting: list[str]) -> float:
    if not supporting:
        return 1.0
    return round(len(set(found) & set(supporting)) / len(supporting), 3)


def _blob(payload: object) -> str:
    return json.dumps(payload, sort_keys=True)


def evaluate(path: Path | None = None) -> dict:
    cases = [json.loads(line) for line in (path or CASES).read_text().splitlines() if line.strip()]
    results = []
    leaks = 0
    for case in cases:
        caller = DIRECTORY[case["caller"]]
        forbid = list(case.get("forbid", [])) + [s for s in SECRETS if s not in case.get("allow_secrets", [])]
        chosen, note = plan(case["question"])
        hybrid_hits = [h.public() for h in hybrid.search(case["question"], caller)]
        graph_hits = [h.public() for h in graph.answer(case["question"], caller)]
        topic_rows = graph.topics(caller) if chosen == "topics" else []
        loop_ids, loop_searches = hybrid_loop(case["question"], caller)
        supporting = case.get("supporting", [])
        hybrid_ids = [h["doc_id"] for h in hybrid_hits]
        graph_ids = sorted({h["doc_id"] for h in graph_hits})
        blob = _blob({"hybrid": hybrid_hits, "graph": graph_hits, "topics": topic_rows})
        found = [secret for secret in forbid if secret in blob]
        leaks += len(found)
        results.append(
            {
                "id": case["id"],
                "chosen": chosen,
                "note": note,
                "expect_mode": case.get("expect_mode", chosen),
                "hybrid_ids": hybrid_ids,
                "graph_ids": graph_ids,
                "hybrid_recall": _recall(hybrid_ids, supporting),
                "graph_recall": _recall(graph_ids, supporting),
                "loop_ids": loop_ids,
                "loop_searches": loop_searches,
                "loop_recall": _recall(loop_ids, supporting),
                "planned_searches": 1,
                "topics": [t["topic"] for t in topic_rows],
                "leaked": found,
            }
        )
    multi = next(r for r in results if r["id"] == "multihop-coating")
    summary = {
        "hybrid_multihop_recall": multi["hybrid_recall"],
        "graph_multihop_recall": multi["graph_recall"],
        "hybrid_loop_searches": multi["loop_searches"],
        "hybrid_loop_recall": multi["loop_recall"],
        "planned_searches": multi["planned_searches"],
        "leaks": leaks,
        "cases": len(results),
    }
    return {
        "note": "Offline comparison on a synthetic corpus. Not a live Azure AI Search or Foundry IQ score.",
        "summary": summary,
        "cases": results,
    }


def render(report: dict | None = None) -> str:
    report = report or evaluate()
    s = report["summary"]
    lines = [
        f"hybrid_multihop_recall={s['hybrid_multihop_recall']}",
        f"graph_multihop_recall={s['graph_multihop_recall']}",
        f"hybrid_loop_searches={s['hybrid_loop_searches']}",
        f"hybrid_loop_recall={s['hybrid_loop_recall']}",
        f"planned_searches={s['planned_searches']}",
        f"leaks={s['leaks']}",
    ]
    for case in report["cases"]:
        lines.append(
            f"{case['id']} chosen={case['chosen']} hybrid={case['hybrid_recall']} graph={case['graph_recall']} leaked={case['leaked']}"
        )
    return "\n".join(lines)
