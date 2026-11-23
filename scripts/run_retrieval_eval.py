"""Compare hybrid retrieval with the knowledge graph and fail on leakage or a recall drop.

python scripts/run_retrieval_eval.py            # print the report and write evals/retrieval-scores.json
python scripts/run_retrieval_eval.py --check    # fail if the committed report is stale or below the bar
python scripts/run_retrieval_eval.py --no-write # print only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "retrieval-scores.json"


def main() -> int:
    from aiip.retrieval.evalcmp import evaluate, render

    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()
    report = evaluate()
    summary = report["summary"]
    print(render(report))
    failed = []
    if summary["graph_multihop_recall"] < 1:
        failed.append("graph multi-hop recall dropped")
    if summary["hybrid_multihop_recall"] >= 1:
        failed.append("hybrid single-shot unexpectedly recalled every multi-hop document")
    if summary["hybrid_loop_searches"] < 3 or summary["hybrid_loop_recall"] < 1:
        failed.append("hybrid follow-up loop did not reach the graph's documents in more searches")
    if summary["planned_searches"] != 1:
        failed.append("planned retrieval was not a single search")
    if summary["leaks"]:
        failed.append(f"leaked strings: {summary['leaks']}")
    for case in report["cases"]:
        if case["chosen"] != case["expect_mode"]:
            failed.append(f"{case['id']} planned {case['chosen']} expected {case['expect_mode']}")
        if case["leaked"]:
            failed.append(f"{case['id']} leaked {case['leaked']}")
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUT.exists() or OUT.read_text() != text:
            failed.append("evals/retrieval-scores.json is stale (run python scripts/run_retrieval_eval.py)")
    elif not args.no_write:
        OUT.write_text(text)
    if failed:
        print("\n".join(failed), file=sys.stderr)
        return 1
    print("retrieval eval ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
