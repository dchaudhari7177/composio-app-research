"""Turn 100 rows into patterns: counts the case-study page states as findings."""

from __future__ import annotations

from collections import Counter, defaultdict

VERDICTS = ("build_now", "build_with_gate", "outreach", "not_viable", "unverified")
ACCESS = ("self_serve", "paid_plan", "approval", "partner", "no_api")


def analyze(results: dict, stage: str = "reviewed") -> dict:
    apps = [a for a in results["apps"] if a.get(stage) or a.get("final")]
    rows = [(a, a.get(stage) or a["final"]) for a in apps]
    n = len(rows)

    auth_count = Counter()
    auth_combo = Counter()
    for a, r in rows:
        for m in r.get("auth") or []:
            auth_count[m] += 1
        auth_combo["+".join(sorted(r.get("auth") or [])) or "none"] += 1

    verdict = Counter(r.get("verdict") or "unknown" for _, r in rows)
    access = Counter(r.get("access") or "unknown" for _, r in rows)

    by_cat: dict[str, Counter] = defaultdict(Counter)
    access_by_cat: dict[str, Counter] = defaultdict(Counter)
    for a, r in rows:
        by_cat[a["category"]][r.get("verdict") or "unknown"] += 1
        access_by_cat[a["category"]][r.get("access") or "unknown"] += 1

    mcp_yes = [a["name"] for a, r in rows if r.get("official_mcp") == "yes"]
    mcp_by_cat = Counter(a["category"] for a, r in rows if r.get("official_mcp") == "yes")
    # Apps where the vendor's MCP is easier to reach than its REST API
    mcp_more_open = [a["name"] for a, r in rows if r.get("official_mcp") == "yes" and r.get("access") in ("partner", "paid_plan", "approval")]
    # Self-serve API, no official MCP yet: where a toolkit adds the most value fastest
    easy_wins = [a["name"] for a, r in rows if r.get("verdict") == "build_now" and r.get("official_mcp") != "yes"]
    outreach = [a["name"] for a, r in rows if r.get("verdict") == "outreach"]
    gated = [a["name"] for a, r in rows if r.get("verdict") == "build_with_gate"]
    not_viable = [a["name"] for a, r in rows if r.get("verdict") == "not_viable"]
    oauth_only = [a["name"] for a, r in rows if (r.get("auth") or []) == ["oauth2"]]
    key_only = [a["name"] for a, r in rows if (r.get("auth") or []) == ["api_key"]]
    graphql = [a["name"] for a, r in rows if r.get("api_type") in ("graphql", "both")]
    community = sum(a.get("community_mcp_servers", 0) for a, _ in rows)
    community_apps = sum(1 for a, _ in rows if a.get("community_mcp_servers", 0) > 0)

    return {
        "n": n,
        "auth_count": dict(auth_count.most_common()),
        "auth_combo": dict(auth_combo.most_common()),
        "verdict": {v: verdict.get(v, 0) for v in VERDICTS} | ({"unknown": verdict["unknown"]} if verdict.get("unknown") else {}),
        "access": {v: access.get(v, 0) for v in ACCESS} | ({"unknown": access["unknown"]} if access.get("unknown") else {}),
        "by_category": {c: {v: by_cat[c].get(v, 0) for v in VERDICTS} for c in by_cat},
        "access_by_category": {c: dict(access_by_cat[c]) for c in access_by_cat},
        "official_mcp": {"count": len(mcp_yes), "apps": mcp_yes, "by_category": dict(mcp_by_cat)},
        "mcp_more_open_than_api": mcp_more_open,
        "easy_wins": easy_wins,
        "outreach": outreach,
        "gated": gated,
        "not_viable": not_viable,
        "oauth_only": oauth_only,
        "key_only": key_only,
        "graphql": graphql,
        "community_mcp": {"servers": community, "apps": community_apps},
    }
