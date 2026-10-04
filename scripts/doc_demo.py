"""Run a script and print its output with run-specific values masked, for pasting into docs.

    python scripts/doc_demo.py demo 3 4      # sections 3 and 4 of `scripts/demo.py --inproc`
    python scripts/doc_demo.py demo          # the whole in-process demo
    python scripts/doc_demo.py safety        # `scripts/run_safety_evals.py --no-write`
    python scripts/doc_demo.py evals         # `scripts/run_eval_gate.py --no-write`

Timings, timestamps, instance ids and correlation ids change on every run; they are replaced
with stable tokens so `scripts/doc_drift.py --check` can compare the docs with a fresh run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {
    "demo": ["scripts/demo.py", "--inproc", "--out", ".demo-logs/doc-demo.json"],
    "safety": ["scripts/run_safety_evals.py", "--no-write"],
    "evals": ["scripts/run_eval_gate.py", "--no-write"],
}
MASKS = [
    (re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?\+00:00"), "<timestamp>"),
    (re.compile(r"\b\d+(\.\d+)? ms\b"), "<n> ms"),
    (re.compile(r"\b(p50|max)=\d+(\.\d+)? ms"), r"\1=<n> ms"),
    (re.compile(r"in \d+(\.\d+)? s\b"), "in <n> s"),
    (re.compile(r"\b[0-9a-f]{32}\b"), "<id>"),
    (re.compile(r"graph-run-[0-9a-f]{12}"), "graph-run-<id>"),
    (re.compile(r"instance [0-9a-f]{8}\.\.\."), "instance <id>..."),
    (re.compile(r'"correlation_id": "[0-9a-f]+'), '"correlation_id": "<id>'),
]


def mask(text: str) -> str:
    for pat, repl in MASKS:
        text = pat.sub(repl, text)
    return text


def sections(text: str, wanted: set[str]) -> str:
    keep, out = False, []
    for line in text.splitlines():
        m = re.match(r"=== (\d+)\.", line)
        if m:
            keep = m.group(1) in wanted
        elif line.startswith("Demo finished"):
            keep = False
        if keep:
            out.append(line)
    return "\n".join(out).strip("\n")


def main(argv: list[str]) -> int:
    name, picks = argv[0], set(argv[1:])
    proc = subprocess.run(
        [sys.executable, *COMMANDS[name]], cwd=ROOT, capture_output=True, text=True, check=False
    )
    text = mask(proc.stdout)
    print(sections(text, picks) if picks else text.rstrip("\n"))
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
