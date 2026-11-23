"""Field-level accuracy against the hand-checked gold sample (data/gold.json)."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCORED = ("auth", "access", "api_type", "official_mcp", "verdict")


AUTH_METHODS = ("oauth2", "api_key", "basic", "other")


def compare(pred: dict, gold: dict) -> dict[str, bool]:
    """auth is multi-label, so it is scored as one yes/no decision per method
    (auth:oauth2, auth:api_key, ...). The strict exact-set match is reported too
    (auth_exact) but kept out of the headline accuracy."""
    out = {}
    for f in SCORED:
        g = gold.get(f)
        if g is None:  # field not confirmable from public docs -> not scored
            continue
        p = pred.get(f)
        if f == "auth":
            ps, gs = set(p or []), set(g)
            for m in AUTH_METHODS:
                out[f"auth:{m}"] = (m in ps) == (m in gs)
        else:
            out[f] = p == g
    return out


def exact_auth(pred: dict, gold: dict):
    if gold.get("auth") is None:
        return None
    return sorted(pred.get("auth") or []) == sorted(gold["auth"])


def score(results: dict, gold: dict, stages=("pass1", "final", "reviewed")) -> dict:
    by_id = {r["id"]: r for r in results["apps"]}
    report: dict = {"n_apps": len(gold["apps"]), "stages": {}}
    for stage in stages:
        rows, tot, hit = [], 0, 0
        per_field = {f: [0, 0] for f in ("auth", "access", "api_type", "official_mcp", "verdict", "auth_exact")}
        for g in gold["apps"]:
            rec = by_id.get(g["id"], {})
            pred = rec.get(stage) or (rec.get("final") if stage == "reviewed" else None) or {}
            cmp = compare(pred, g)
            for f, ok in cmp.items():
                k = "auth" if f.startswith("auth:") else f
                per_field[k][0] += ok
                per_field[k][1] += 1
            ex = exact_auth(pred, g)
            if ex is not None:
                per_field["auth_exact"][0] += ex
                per_field["auth_exact"][1] += 1
            tot += len(cmp)
            hit += sum(cmp.values())
            view = {f: ok for f, ok in cmp.items() if not f.startswith("auth:")}
            if ex is not None:
                view["auth"] = ex  # the page shows the strict per-app view of auth
            rows.append({"id": g["id"], "name": g["name"], "pred": {f: pred.get(f) for f in SCORED},
                         "gold": {f: g.get(f) for f in SCORED}, "ok": view, "ok_methods": cmp})
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
