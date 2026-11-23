"""Field-level accuracy against the hand-checked gold sample (data/gold.json)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCORED = ("auth", "access", "api_type", "official_mcp", "verdict")


def compare(pred: dict, gold: dict) -> dict[str, bool]:
    out = {}
    for f in SCORED:
        g = gold.get(f)
        if g is None:  # field not confirmable from public docs -> not scored
            continue
        p = pred.get(f)
        if f == "auth":
            out[f] = sorted(p or []) == sorted(g)
        else:
            out[f] = p == g
    return out


def score(results: dict, gold: dict, stages=("pass1", "final", "reviewed")) -> dict:
    by_id = {r["id"]: r for r in results["apps"]}
    report: dict = {"n_apps": len(gold["apps"]), "stages": {}}
    for stage in stages:
        rows, tot, hit = [], 0, 0
        per_field = {f: [0, 0] for f in SCORED}
        for g in gold["apps"]:
            rec = by_id.get(g["id"], {})
            pred = rec.get(stage) or (rec.get("final") if stage == "reviewed" else None) or {}
            cmp = compare(pred, g)
            for f, ok in cmp.items():
                per_field[f][0] += ok
                per_field[f][1] += 1
            tot += len(cmp)
            hit += sum(cmp.values())
            rows.append({"id": g["id"], "name": g["name"], "pred": {f: pred.get(f) for f in SCORED},
                         "gold": {f: g.get(f) for f in SCORED}, "ok": cmp})
        report["stages"][stage] = {
            "accuracy": round(hit / tot, 3) if tot else None, "correct": hit, "scored_fields": tot,
            "per_field": {f: {"correct": c, "of": n, "accuracy": round(c / n, 3) if n else None} for f, (c, n) in per_field.items()},
            "rows": rows,
        }
    return report


def score_file(path: Path) -> dict:
    results = json.loads(path.read_text(encoding="utf-8"))
    gold = json.loads((ROOT / "data" / "gold.json").read_text(encoding="utf-8"))
    rep = score(results, gold)
    (ROOT / "runs" / "accuracy.json").write_text(json.dumps(rep, indent=1), encoding="utf-8")
    return {s: {k: v for k, v in d.items() if k != "rows"} for s, d in rep["stages"].items()}
