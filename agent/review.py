"""Human review queue.

The agent never guesses on a conflict: flagged fields land in a queue
(runs/review_queue.json). A person resolves them in data/review.json, each
decision carrying the docs URL and quote it rests on. `apply` writes the
reviewed answer next to the agent's own, so both stay visible.
"""

from __future__ import annotations

import json
from pathlib import Path

from .research import verdict_for

ROOT = Path(__file__).resolve().parent.parent

NEEDS_HUMAN = {"conflict", "rule_only", "missing", "unverified"}


def queue(results: dict) -> list[dict]:
    q = []
    for rec in results["apps"]:
        if "final" not in rec:
            q.append({"id": rec["id"], "name": rec["name"], "field": "*", "kind": "error", "detail": rec.get("error", "")})
            continue
        for fl in rec.get("flags", []):
            if fl["kind"] in NEEDS_HUMAN:
                q.append({"id": rec["id"], "name": rec["name"], "field": fl["field"], "kind": fl["kind"],
                          "agent_value": fl.get("value"), "detail": fl["detail"], "docs_url": rec.get("docs_url")})
    return q


def apply(results: dict, decisions: list[dict]) -> dict:
    by_id = {r["id"]: r for r in results["apps"]}
    for rec in results["apps"]:
        rec["reviewed"] = dict(rec.get("final") or {})
        rec["review"] = []
    for d in decisions:
        rec = by_id.get(d["id"])
        if not rec:
            continue
        rv = rec["reviewed"]
        rv[d["field"]] = d["value"]
        rec["review"].append(d)
    for rec in results["apps"]:
        rv = rec["reviewed"]
        if rv.get("access") == "no_api":
            rv["api_type"], rv["auth"] = "none", []
        rv["verdict"] = verdict_for(rv.get("access"), rv.get("api_type"))
    return results


def main(results_path: Path):
    results = json.loads(results_path.read_text(encoding="utf-8"))
    q = queue(results)
    (ROOT / "runs" / "review_queue.json").write_text(json.dumps(q, indent=1, ensure_ascii=False), encoding="utf-8")
    dpath = ROOT / "data" / "review.json"
    decisions = json.loads(dpath.read_text(encoding="utf-8")) if dpath.exists() else []
    apply(results, decisions)
    results_path.write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"review queue: {len(q)} items; applied {len(decisions)} human decisions")
