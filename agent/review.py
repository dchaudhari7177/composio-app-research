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

NEEDS_HUMAN = {"conflict", "rule_only", "missing", "unverified", "no_evidence"}

# The blocker shown for each access gate when no reviewer note says something more specific.
BLOCKER = {
    "self_serve": "none",
    "paid_plan": "API needs a paid plan",
    "approval": "app review or access approval before production use",
    "partner": "sales-led: contact sales or an enterprise contract",
    "no_api": "no public network API (CLI or library only)",
}


def queue(results: dict) -> list[dict]:
    q = []
    for rec in results["apps"]:
        if "final" not in rec:
            q.append({"id": rec["id"], "name": rec["name"], "field": "*", "kind": "error", "detail": rec.get("error", "")})
            continue
        if not rec.get("pages_read"):
            # Evidence floor: an answer built from zero pages is a guess, whatever the LLM says.
            q.append({"id": rec["id"], "name": rec["name"], "field": "*", "kind": "no_evidence", "agent_value": None,
                      "detail": "the agent could not read a single docs page; every field needs a human",
                      "docs_url": rec.get("docs_url")})
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
        if not rv:
            continue
        if rv.get("access") == "no_api":
            rv["api_type"], rv["auth"] = "none", []
        rv["verdict"] = verdict_for(rv.get("access"), rv.get("api_type"))
        decided = {d["field"] for d in rec["review"]}
        if not rec.get("pages_read") and "access" not in decided:
            # Nothing was read and no human settled the gate: say so instead of shipping a guess.
            rv["verdict"] = "unverified"
        acc_note = next((d for d in rec["review"] if d["field"] == "access"), None)
        if rv["verdict"] == "unverified":
            rec["blocker_final"] = "unresolved: no readable docs, needs a human"
        elif acc_note and acc_note.get("blocker"):
            rec["blocker_final"] = acc_note["blocker"]
        elif acc_note and rv.get("access") != "self_serve" and acc_note.get("note") not in (None, "", "checked by hand in a browser"):
            rec["blocker_final"] = acc_note["note"]
        else:
            rec["blocker_final"] = BLOCKER.get(rv.get("access"), "unknown")
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
