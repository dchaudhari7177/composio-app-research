"""Build the single-page case study (site/index.html) plus machine-readable twins.

Everything on the page is computed from runs/results.json, runs/accuracy.json,
data/gold.json and data/findings.md, so the page cannot drift from the data.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

from .patterns import analyze
from .review import queue
from .score import score

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"


def slim(rec: dict) -> dict:
    """The per-app record the page and apps.json expose."""
    fin = rec.get("reviewed") or rec.get("final") or {}
    ev = {}
    for k, lst in (rec.get("evidence") or {}).items():
        ev[k] = [{"url": e["url"], "quote": e["quote"][:280], "value": e["value"]} for e in lst[:2]]
    for d in rec.get("review", []):
        ev.setdefault(d["field"], [])
        ev[d["field"]] = [{"url": d["url"], "quote": d["quote"][:280], "value": d["value"], "human": True}] + ev[d["field"]][:1]
    return {
        "id": rec["id"], "name": rec["name"], "category": rec["category"], "hint": rec["hint"],
        "docs_url": rec.get("docs_url"), "one_liner": (rec.get("one_liner") or "").strip(),
        "blocker": rec.get("blocker") or "",
        "auth": fin.get("auth") or [], "access": fin.get("access"), "api_type": fin.get("api_type"),
        "official_mcp": fin.get("official_mcp"), "verdict": fin.get("verdict"),
        "agent": rec.get("final"), "pass1": rec.get("pass1"),
        "evidence": ev,
        "flags": [{"field": f["field"], "kind": f["kind"], "detail": f["detail"]} for f in rec.get("flags", [])],
        "reviewed_fields": [d["field"] for d in rec.get("review", [])],
        "community_mcp_servers": rec.get("community_mcp_servers", 0),
        "pages_read": rec.get("pages_read", 0), "pages_rendered": rec.get("pages_rendered", 0),
        "trace": rec.get("trace", []),
    }


def md_inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"`(.+?)`", r"<code>\1</code>", s)
    return s


def findings_html(path: Path) -> str:
    """data/findings.md: '## headline' then a paragraph, per finding."""
    if not path.exists():
        return ""
    blocks = re.split(r"^## ", path.read_text(encoding="utf-8"), flags=re.M)[1:]
    out = []
    for i, b in enumerate(blocks, 1):
        head, _, body = b.partition("\n")
        out.append(f'<li class="finding"><span class="fnum">{i:02d}</span><div><h3>{md_inline(head.strip())}</h3>'
                   f'<p>{md_inline(" ".join(body.split()))}</p></div></li>')
    return "\n".join(out)


def build(results_path: Path):
    results = json.loads(results_path.read_text(encoding="utf-8"))
    gold = json.loads((ROOT / "data" / "gold.json").read_text(encoding="utf-8"))
    acc = score(results, gold)
    pat = analyze(results)
    q = queue(results)
    apps = [slim(r) for r in results["apps"] if "final" in r]
    cov_path = ROOT / "data" / "composio_coverage.json"
    cov = {c["id"]: c for c in json.loads(cov_path.read_text(encoding="utf-8"))["apps"]} if cov_path.exists() else {}
    for a in apps:
        c = cov.get(a["id"], {})
        a["composio"] = c.get("composio_toolkits", [])
        a["composio_note"] = c.get("note", "")
    gaps = [a for a in apps if not a["composio"]]
    pat["composio"] = {
        "covered": len(apps) - len(gaps),
        "gaps": [{"name": a["name"], "verdict": a["verdict"], "access": a["access"], "official_mcp": a["official_mcp"]} for a in gaps],
        "mcp_wrapped": [a["name"] for a in apps if a["composio"] and all(t.endswith("_mcp") for t in a["composio"])],
    }
    totals = {
        "pages_read": sum(a["pages_read"] for a in apps),
        "pages_rendered": sum(a["pages_rendered"] for a in apps),
        "flags": sum(len(a["flags"]) for a in apps),
        "review_queue": len(q),
        "reviewed": sum(len(a["reviewed_fields"]) for a in apps),
        "llm": results.get("llm"), "generated": results.get("generated"), "fetch": results.get("fetch_stats"),
        "flag_kinds": {},
    }
    for a in apps:
        for f in a["flags"]:
            totals["flag_kinds"][f["kind"]] = totals["flag_kinds"].get(f["kind"], 0) + 1
    payload = {
        "apps": apps, "patterns": pat, "totals": totals,
        "accuracy": {s: {k: v for k, v in d.items()} for s, d in acc["stages"].items()},
        "gold": {"ids": [g["id"] for g in gold["apps"]], "corrections": gold.get("corrections", []), "about": gold["about"]},
        "queue": q,
    }
    SITE.mkdir(exist_ok=True)
    tpl = (Path(__file__).parent / "template.html").read_text(encoding="utf-8")
    data_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    page = tpl.replace("/*__DATA__*/null", data_json).replace("<!--__FINDINGS__-->", findings_html(ROOT / "data" / "findings.md"))
    (SITE / "index.html").write_text(page, encoding="utf-8")
    (SITE / "apps.json").write_text(json.dumps({"schema": "see /llms.txt", "apps": apps}, indent=1, ensure_ascii=False), encoding="utf-8")
    (SITE / "llms.txt").write_text(llms_txt(apps, pat, acc), encoding="utf-8")
    print(f"site built: {len(apps)} apps, page {len(page) // 1024} KB")


def llms_txt(apps: list[dict], pat: dict, acc: dict) -> str:
    fa = acc["stages"]["final"]["accuracy"]
    lines = [
        "# App toolkit readiness: 100 SaaS apps",
        "",
        "> Agent-researched auth, access gate, API surface and official MCP status for 100 apps, with evidence URLs.",
        f"> Field accuracy on a 20-app hand-checked sample: {fa:.0%} (agent, before human review).",
        "",
        "## Data",
        "- [apps.json](/apps.json): one record per app. Fields: auth (oauth2|api_key|basic|other), access (self_serve|paid_plan|approval|partner|no_api), api_type (rest|graphql|both|none), official_mcp (yes|no), verdict (build_now|build_with_gate|outreach|not_viable), evidence {field: [{url, quote}]}.",
        "",
        "## Verdict per app",
    ]
    for a in apps:
        lines.append(f"- {a['name']} ({a['category']}): {a['verdict']}; auth={'+'.join(a['auth']) or 'n/a'}; access={a['access']}; mcp={a['official_mcp']}")
    return "\n".join(lines) + "\n"
