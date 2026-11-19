"""One governed path for grounding and actions.

REST, MCP and A2A all call these functions. Passage text is screened here, with no tools in
reach, before it is returned. The product describes an isolated model for that step; offline,
the regex screen is the stand-in and the isolated model is [verify].
"""

from __future__ import annotations

from aiip.retrieval import governance, graph, hybrid
from aiip.retrieval.corpus import DIRECTORY, Caller
from aiip.retrieval.permissions import visible_passages, withheld_counts
from aiip.retrieval.planner import plan
from aiip.shared import errors as E
from aiip.shared.auth import Principal
from aiip.shared.untrusted import screen

_LISTEN_ACTOR = "context-listen"


def caller_for(principal: Principal, trusted_user: str | None = None) -> Caller:
    """The document subject. A body field cannot choose it.

    Only the Listen worker may bind the user named on an event. Everyone else is the user on
    the token, or, for an app-only token, a caller with no groups (fail closed).
    """
    if trusted_user is not None:
        if principal.actor != _LISTEN_ACTOR:
            raise E.GatewayError(E.AUTHZ_DENY, "only the listen worker may bind an event user")
        return DIRECTORY.get(trusted_user, Caller.empty(trusted_user))
    if not principal.is_user:
        return Caller.empty(principal.actor)
    username = principal.subject.split("@", 1)[0]
    return DIRECTORY.get(username, Caller.empty(username))


def _clean(text: str) -> str:
    cleaned, _flags = screen(text)
    return cleaned if isinstance(cleaned, str) else str(cleaned)


def ground(
    principal: Principal,
    question: str,
    mode: str = "plan",
    trusted_user: str | None = None,
) -> dict:
    """Retrieve for this caller. ``mode`` is hybrid, graph, topics, gql or plan."""
    policy = governance.require(principal, "read")
    caller = caller_for(principal, trusted_user)
    chosen = mode
    note = ""
    searches = 1
    if mode == "plan":
        chosen, note = plan(question)
        searches = 1
    hits: list[dict] = []
    topics: list[dict] = []
    rows: list[dict[str, str]] = []
    if chosen == "hybrid":
        hits = [_project(h.public(), policy.domains) for h in hybrid.search(question, caller)]
    elif chosen == "graph":
        hits = [_project(h.public(), policy.domains) for h in graph.answer(question, caller)]
    elif chosen == "topics":
        topics = _topics(caller, policy.domains)
    elif chosen == "gql":
        rows = graph.query_gql(question, caller)
    else:
        raise E.GatewayError(E.VALIDATION, "mode must be hybrid, graph, topics, gql or plan")
    hits = [h for h in hits if h]
    withheld = withheld_counts(caller)
    result = {
        "mode": chosen,
        "searches": searches,
        "note": note,
        "hits": hits,
        "topics": topics,
        "rows": rows,
        "withheld": withheld,
        "doc_ids": sorted({h["doc_id"] for h in hits}),
    }
    governance.record(principal, "ground", E.OK, chosen)
    return result


def _project(hit: dict, domains: frozenset[str]) -> dict:
    from aiip.retrieval.corpus import BY_ID

    passage = BY_ID.get(hit["doc_id"])
    if passage is None or passage.domain not in domains:
        return {}
    projected = dict(hit)
    projected["text"] = _clean(str(hit.get("text", "")))
    return projected


def _topics(caller: Caller, domains: frozenset[str]) -> list[dict]:
    from aiip.retrieval.corpus import BY_ID

    visible = {p.doc_id for p in visible_passages(caller) if p.domain in domains}
    kept = []
    for topic in graph.topics(caller):
        doc_ids = [doc_id for doc_id in topic["doc_ids"] if doc_id in visible]
        if not doc_ids:
            continue
        titles = [BY_ID[doc_id].title for doc_id in doc_ids]
        kept.append({"topic": topic["topic"], "doc_ids": doc_ids, "titles": titles})
    return kept


def act(principal: Principal, verb: str, resource: str, body: str = "", domain: str = "sales") -> dict:
    """Create or update a work item. Delete and anything above the ceiling raises."""
    if verb == "read":
        raise E.GatewayError(E.VALIDATION, "read is a grounding call, not an action")
    governance.require(principal, verb)
    governance.domain_allowed(principal, domain)
    if verb == "create":
        item = governance.put_item(resource, body, principal.subject, domain)
    elif verb == "update":
        if governance.get_item(resource) is None:
            raise E.GatewayError(E.NOT_FOUND, "no such work item")
        item = governance.put_item(resource, body, principal.subject, domain)
    else:
        raise E.GatewayError(E.AUTHZ_DENY, "above the admin ceiling")
    governance.record(principal, verb, E.OK, resource)
    return {"ok": True, "verb": verb, "item": item}
