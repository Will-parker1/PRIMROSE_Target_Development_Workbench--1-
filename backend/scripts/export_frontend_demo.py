#!/usr/bin/env python3
"""Export a deterministic, read-only frontend fallback snapshot.

The hosted Sites preview cannot start the local Python process.  This exporter
captures the same API shapes so the UI remains inspectable, with an explicit
demo-mode banner.  Local development proxies to the live Python API instead.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import Application  # noqa: E402
from target_development import TargetScreeningError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    application = Application(ROOT / "graph.graphml", ROOT / "runtime" / "demo_review_state.json")
    bootstrap = application.workbench.bootstrap()
    previews: dict[str, object] = {}
    queued_rows = [
        row
        for queue in ("collect", "monitor", "act", "challenge", "hypotheses")
        for row in bootstrap["queues"][queue]
    ]
    candidate_rows = queued_rows + application.workbench.target_development.candidates(limit=80)["items"]
    seen: set[str] = set()
    for row in candidate_rows:
        if len(previews) >= 10:
            break
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        if row["screening"].get("verdict") != "developable":
            continue
        try:
            previews[row["id"]] = application.workbench.target_development.preview(
                row["id"], phase="basic"
            )
        except (TargetScreeningError, ValueError, KeyError):
            continue

    payload = {
        "mode": "hosted-demo-snapshot",
        "notice": "SYNTHETIC / TRAINING / NOT OPERATIONAL — connect PRIMROSE_BACKEND_URL for live Python data.",
        "bootstrap": bootstrap,
        "methods": application.workbench.methods(),
        "profiles": application.workbench.profiles(),
        "adapters": application.workbench.adapters(),
        "target_candidates": application.workbench.target_development.candidates(limit=80),
        "target_previews": previews,
        "audit": {"items": []},
        "health": {
            "status": "demo",
            "application": "PRIMROSE hosted snapshot",
            "graph_loaded": True,
            "extraction": {"available": False, "reason": "Live Python backend not connected."},
            "reasoning": {
                "available": False,
                "endpoint_reachable": False,
                "model_id": "google/gemma-4-e4b",
                "reason": "Gemma-4 is not configured; deterministic fallback remains available.",
            },
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {args.output} ({args.output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
