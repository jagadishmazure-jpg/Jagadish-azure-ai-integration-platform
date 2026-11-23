"""Azure AI Search-style hybrid retrieval, offline.

Keyword rank is BM25. The vector channel is a hashing-trick stand-in: there is no embedding
model in this repo, so a passage scores on the vector channel only when it shares a token with
the question. The two ranked lists are fused with reciprocal rank fusion (RRF, k=60), which is
the fusion Azure AI Search uses for hybrid queries. This does not call Azure AI Search.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from aiip.retrieval.corpus import Caller, Passage
from aiip.retrieval.permissions import visible_passages
from aiip.retrieval.textutil import tokens

RRF_K = 60
DIM = 64


@dataclass(frozen=True)
class Hit:
    doc_id: str
    title: str
    text: str
    score: float
    citation: str

    def public(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "text": self.text,
            "score": round(self.score, 6),
            "citation": self.citation,
        }


def _embed(text: str) -> list[float]:
    vec = [0.0] * DIM
    for tok in tokens(text):
        digest = hashlib.sha256(tok.encode()).digest()
        idx = int.from_bytes(digest[:2], "big") % DIM
        vec[idx] += 1.0 if digest[2] % 2 == 0 else -1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _bm25(query: list[str], docs: list[tuple[str, list[str]]]) -> dict[str, float]:
    n = len(docs)
    if not n:
        return {}
    avgdl = sum(len(toks) for _, toks in docs) / n
    df: dict[str, int] = {}
    counted: list[dict[str, int]] = []
    for _, toks in docs:
        tf: dict[str, int] = {}
        for tok in toks:
            tf[tok] = tf.get(tok, 0) + 1
        counted.append(tf)
        for term in tf:
            df[term] = df.get(term, 0) + 1
    k1, b = 1.2, 0.75
    scores: dict[str, float] = {}
    for (doc_id, toks), tf in zip(docs, counted, strict=True):
        score = 0.0
        dl = len(toks) or 1
        for term in set(query):
            freq = tf.get(term, 0)
            if not freq:
                continue
            n_q = df[term]
            idf = math.log(1 + (n - n_q + 0.5) / (n_q + 0.5))
            score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * dl / avgdl))
        if score > 0:
            scores[doc_id] = score
    return scores


def reciprocal_rank_fusion(rank_lists: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Fuse ranked id lists. Higher is better. Ties break on the id so the order is stable."""
    scores: dict[str, float] = {}
    for ranks in rank_lists:
        for rank, doc_id in enumerate(ranks, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _rank(scores: dict[str, float]) -> list[str]:
    return [doc_id for doc_id, _ in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]


def search(question: str, caller: Caller | None, k: int = 5) -> list[Hit]:
    """Hybrid search over passages this caller may read. Hidden passages are not scored."""
    visible = visible_passages(caller)
    return _search_passages(question, visible, k)


def _search_passages(question: str, visible: list[Passage], k: int) -> list[Hit]:
    q = tokens(question)
    if not q or not visible:
        return []
    docs = [(p.doc_id, tokens(p.text + " " + p.title)) for p in visible]
    lexical = _bm25(q, docs)
    qv = _embed(question)
    vector: dict[str, float] = {}
    qset = set(q)
    for passage, (_, doc_tokens) in zip(visible, docs, strict=True):
        if not (qset & set(doc_tokens)):
            continue
        cosine = _cosine(qv, _embed(passage.text + " " + passage.title))
        if cosine > 0:
            vector[passage.doc_id] = cosine
    fused = reciprocal_rank_fusion([_rank(lexical), _rank(vector)])
    by_id = {p.doc_id: p for p in visible}
    hits = []
    for doc_id, score in fused[:k]:
        passage = by_id[doc_id]
        hits.append(Hit(passage.doc_id, passage.title, passage.text, score, f"corpus://{passage.doc_id}"))
    return hits
