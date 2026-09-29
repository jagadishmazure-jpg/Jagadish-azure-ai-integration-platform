"""Runtime-safety eval gate: attack scenarios must all be contained and benign scenarios must
never be quarantined. Prints per-scenario results and the measured containment latency.

    python scripts/run_safety_evals.py            # also writes evals/safety-scores.json
    python scripts/run_safety_evals.py --no-write # CI"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AIIP_MODE", "local")
for k in [k for k in os.environ if k.startswith("AIIP_") and k.endswith("_URL")]:
    os.environ.pop(k)
logging.getLogger("a2a.server.events.event_queue_v2").setLevel(logging.ERROR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-write", action="store_true", help="do not update evals/safety-scores.json")
    a = ap.parse_args()
    from aiip.safety.evals import run_all

    report = asyncio.run(run_all())
    for r in report["scenarios"]:
        mark = "PASS" if r["passed"] else "FAIL"
        lat = f"{r['latency_ms']:.2f} ms" if r["latency_ms"] is not None else "-"
        print(
            f"  {mark} {r['id']:40s} quarantined={r['quarantined']!s:5s} detector={r['detector'] or '-':18s} latency={lat}"
        )
    s = report["summary"]
    print(
        f"\nattacks contained: {s['contained']}/{s['attacks']} (rate {s['containment_rate']}), "
        f"false quarantines: {s['false_quarantines']}/{s['benign']}, "
        f"containment latency p50={s['latency_ms']['p50']} ms max={s['latency_ms']['max']} ms"
    )
    if not a.no_write:
        out = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), **report}
        (ROOT / "evals" / "safety-scores.json").write_text(json.dumps(out, indent=2) + "\n")
        print("wrote evals/safety-scores.json")
    print("SAFETY GATE " + ("PASSED" if s["gate_passed"] else "FAILED"))
    return 0 if s["gate_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
