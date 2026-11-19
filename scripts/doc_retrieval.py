"""Stable retrieval demo for the docs. Offline, no network, no live search service."""

from __future__ import annotations

import asyncio

from aiip.retrieval import reset
from aiip.retrieval.evalcmp import render
from aiip.retrieval.listen import start_run


async def main() -> None:
    reset()
    print(render())
    run = await start_run(
        {
            "id": "evt-doc",
            "type": "com.contoso.work.message.received.v1",
            "data": {"user": "olivia", "text": "What must we do about regulation EU-COATING-2026?"},
        }
    )
    assert run is not None
    print(
        f"listen outcome={run['outcome']} mode={run['mode']} docs={','.join(run['doc_ids'])} run={run['run_id']}"
    )


if __name__ == "__main__":
    asyncio.run(main())
