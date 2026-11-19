"""Permission-trimmed grounding.

A passage is returned only when the caller matches its ACL (user, Entra group, or SharePoint-style
site group). Missing ACLs fail closed. Confidential passages also fail closed unless a label
policy was verified. That label check is a stand-in for Purview sensitivity-label enforcement,
which is public preview [verify].
"""

from __future__ import annotations

from aiip.retrieval.corpus import PASSAGES, Caller, Passage


def allowed(passage: Passage, caller: Caller | None) -> bool:
    """True only when this caller may read this passage. None and a missing ACL are denials."""
    if caller is None or passage.acl is None:
        return False
    acl = passage.acl
    identity = caller.user in acl.users or bool(caller.entra & acl.entra) or bool(caller.sites & acl.sites)
    if not identity:
        return False
    if acl.container_readers and not (caller.container_roles & acl.container_readers):
        return False
    return passage.sensitivity != "confidential" or caller.label_policy_ok


def visible_passages(caller: Caller | None, passages: tuple[Passage, ...] = PASSAGES) -> list[Passage]:
    return [p for p in passages if allowed(p, caller)]


def withheld_counts(caller: Caller | None, passages: tuple[Passage, ...] = PASSAGES) -> dict[str, int]:
    """How many passages were dropped, without their titles or text."""
    labels = containers = 0
    if caller is None:
        return {"labels": 0, "containers": 0}
    for p in passages:
        if p.acl is None:
            continue
        identity = (
            caller.user in p.acl.users or bool(caller.entra & p.acl.entra) or bool(caller.sites & p.acl.sites)
        )
        if not identity:
            continue
        if p.acl.container_readers and not (caller.container_roles & p.acl.container_readers):
            containers += 1
            continue
        if p.sensitivity == "confidential" and not caller.label_policy_ok:
            labels += 1
    return {"labels": labels, "containers": containers}
