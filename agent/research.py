"""The research agent: one app in, one evidenced record out.

Pass 1 is a naive agent run: find the docs, read the first part of a few pages,
ask the LLM. The verification loops then improve on it:

  L1  targeted evidence   rule-based quotes from every page replace raw page heads
  L2  follow-up crawl     missing fields trigger a second, field-targeted crawl
  L3  browser render      JS-rendered or bot-blocked pages are re-read in headless Edge
  L4  MCP registry        the official MCP registry is checked for a vendor-owned server
  L5  grounding check     every LLM claim must cite a snippet that actually says it
  L6  rule reconciliation the LLM answer is compared with the rule signals; conflicts
                          go to a human review queue instead of being guessed

Both the pass-1 answer and the final answer are kept, so the accuracy gain from
the loops can be measured on the hand-checked sample.
"""

from __future__ import annotations

import asyncio
import re
import threading

import httpx
from urllib.parse import quote_plus, urlparse

from .discover import FIELD_KEYWORDS, candidate_roots, hint_url, looks_like_docs, rank, site_index
from .fetch import Fetcher, Page, same_site
from .llm import LLM
from .signals import Signal, extract, summarize, supports

FIELDS = ("auth", "access", "api_type", "official_mcp")
ACCESS = ("self_serve", "paid_plan", "approval", "partner", "no_api")
AUTH = ("oauth2", "api_key", "basic", "other")
API_TYPES = ("rest", "graphql", "both", "none")

_llm_lock = threading.Lock()


def verdict_for(access: str | None, api_type: str | None) -> str | None:
    if access is None:
        return None
    if access == "no_api" or api_type == "none":
        return "not_viable"
    return {"self_serve": "build_now", "paid_plan": "build_with_gate", "approval": "build_with_gate",
            "partner": "outreach"}.get(access)


def norm_value(field: str, v) -> str | None:
    if v is None:
        return None
    v = str(v).strip().lower().replace(" ", "_").replace("-", "_")
    alias = {"oauth": "oauth2", "oauth_2.0": "oauth2", "oauth2.0": "oauth2", "apikey": "api_key", "api_token": "api_key",
             "token": "api_key", "bearer": "api_key", "bearer_token": "api_key", "personal_access_token": "api_key",
             "http_basic": "basic", "selfserve": "self_serve", "free": "self_serve", "paid": "paid_plan",
             "enterprise": "partner", "contact_sales": "partner", "true": "yes", "false": "no", "restful": "rest"}
    v = alias.get(v, v)
    allowed = {"auth": AUTH, "access": ACCESS, "api_type": API_TYPES, "official_mcp": ("yes", "no")}[field]
    return v if v in allowed else None


def vendor_tokens(app: dict, docs_url: str) -> set[str]:
    """Strings that identify the vendor: domain labels and a slug of the name."""
    toks = set()
    for u in (hint_url(app["hint"]),):
        host = urlparse(u).netloc.lower()
        parts = [p for p in host.split(".") if p not in {"www", "com", "io", "ai", "dev", "co", "app", "org", "net",
                                                       "developers", "developer", "docs", "api", "help", "open", "github", "learn"}]
        toks.update(p for p in parts if len(p) > 2)
    slug = re.sub(r"[^a-z0-9]", "", app["name"].lower().split("(")[0])
    if len(slug) > 2:
        toks.add(slug)
    return toks


async def mcp_registry(f: Fetcher, app: dict, docs_url: str) -> tuple[list[Signal], int]:
    """L4: look the app up in the official MCP registry; return vendor-owned hits + community count."""
    name = app["name"].split("(")[0].strip()
    url = f"https://registry.modelcontextprotocol.io/v0/servers?search={quote_plus(name)}&limit=50"
    page = await f.get(url)
    import json

    try:
        data = json.loads(page.text)
    except Exception:
        return [], 0
    toks = vendor_tokens(app, docs_url)
    official, community = [], set()
    for s in data.get("servers", []):
        srv = s.get("server", s)
        sname = (srv.get("name") or "").lower()
        repo = ((srv.get("repository") or {}).get("url") or "").lower()
        remotes = [r.get("url", "").lower() for r in (srv.get("remotes") or [])]
        ns = sname.split("/")[0]  # reverse-DNS namespace, e.g. com.stripe or io.github.stripe
        owner = repo.split("github.com/")[1].split("/")[0] if "github.com/" in repo else ""
        ns_domain = ".".join(reversed(ns.split(".")))  # com.stripe -> stripe.com
        home = hint_url(app["hint"])
        # Vendor-owned only on an exact match: the namespace IS the vendor's domain, the GitHub
        # repo owner IS the vendor slug (io.github.<vendor>/...), or a remote is served from the
        # vendor's own domain. A third party's "pipedrive.usefulapi.io" does not count.
        vendor_owned = (not ns.startswith("io.github.") and "." in ns and same_site("https://" + ns_domain, home)) or             (bool(owner) and owner.replace("-", "") in toks) or             (ns.startswith("io.github.") and ns.split(".", 2)[-1].replace("-", "") in toks) or             any(same_site(r, home) for r in remotes if r.startswith("http"))
        if vendor_owned:
            official.append(Signal("official_mcp", "yes", f"https://registry.modelcontextprotocol.io/v0/servers?search={quote_plus(name)}",
                                   f"MCP registry entry {srv.get('name')} ({repo or ', '.join(remotes)}): {(srv.get('description') or '')[:160]}", 3))
        else:
            community.add(sname)
    return official[:2], len(community)


_gh_lock = asyncio.Semaphore(2)


async def github_org_mcp(app: dict) -> list[Signal]:
    """L4b: an MCP server repo published under the vendor's own GitHub org.

    Many vendors ship their MCP server on GitHub before (or instead of) their docs site
    mentioning it. One search per app; a hit counts only when the repo owner is the vendor
    (owner login matches the vendor's domain label or name) and the repo name contains 'mcp'.
    """
    import hashlib
    import json
    import os

    from .fetch import CACHE

    toks = vendor_tokens(app, "")
    slug = re.sub(r"[^a-z0-9]", "", app["name"].lower().split("(")[0])
    q = f"{slug} mcp"
    path = CACHE / f"gh-{hashlib.sha1(q.encode()).hexdigest()[:16]}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        headers = {"Accept": "application/vnd.github+json"}
        if os.environ.get("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
        async with _gh_lock:
            try:
                async with httpx.AsyncClient(timeout=20) as c:
                    r = await c.get("https://api.github.com/search/repositories", params={"q": q, "per_page": 30}, headers=headers)
                data = r.json() if r.status_code == 200 else {"items": [], "status": r.status_code}
            except Exception as e:
                data = {"items": [], "error": str(e)[:100]}
            await asyncio.sleep(2.1)  # stay under the search API rate limit
        if "status" not in data and "error" not in data:
            path.write_text(json.dumps({"items": [{"full_name": i["full_name"], "html_url": i["html_url"],
                                                   "description": i.get("description") or ""} for i in data.get("items", [])]}),
                            encoding="utf-8")
    out = []
    for it in data.get("items", []):
        owner, name = it["full_name"].lower().split("/", 1)
        if "mcp" in name and owner.replace("-", "") in toks:
            out.append(Signal("official_mcp", "yes", it["html_url"],
                              f"GitHub repo {it['full_name']} (vendor org): {(it.get('description') or '')[:160]}", 3))
    return out[:2]


def snippet_block(signals: list[Signal], root: Page | None, cap: int = 30) -> tuple[str, dict[str, Signal]]:
    """Pick the strongest, most diverse quotes and number them S1..Sn for the LLM."""
    by_key: dict[tuple[str, str], list[Signal]] = {}
    for s in sorted(signals, key=lambda s: -s.strength):
        by_key.setdefault((s.field, s.value), [])
        if len(by_key[(s.field, s.value)]) < 3 and all(s.quote != t.quote for t in by_key[(s.field, s.value)]):
            by_key[(s.field, s.value)].append(s)
    chosen: list[Signal] = []
    for rnd in range(3):
        for lst in by_key.values():
            if rnd < len(lst):
                chosen.append(lst[rnd])
    chosen = chosen[:cap]
    ids = {f"S{i + 1}": s for i, s in enumerate(chosen)}
    lines = []
    if root is not None and root.text:
        lines.append(f"S0 [{root.final_url or root.url}] {root.title} :: {root.text[:400].replace(chr(10), ' ')}")
    for k, s in ids.items():
        lines.append(f"{k} [{s.url}] {s.quote}")
    return "\n".join(lines), ids


def call_llm(llm: LLM, prompt: str) -> dict:
    with _llm_lock:
        try:
            return llm.complete_json(prompt)
        except Exception as e:
            return {"_error": str(e)[:200]}


def read_llm(out: dict) -> dict:
    """Normalize a raw LLM answer into {field: value(s)} plus cites."""
    res: dict = {"one_liner": (out.get("one_liner") or "")[:160], "blocker": (out.get("blocker") or "")[:120],
                 "api_breadth": (str(out.get("api_breadth") or "").lower() or None)}
    auth = []
    for a in out.get("auth") or []:
        if isinstance(a, dict):
            v = norm_value("auth", a.get("value"))
            if v:
                auth.append({"value": v, "cite": a.get("cite") or []})
        else:
            v = norm_value("auth", a)
            if v:
                auth.append({"value": v, "cite": []})
    res["auth"] = auth
    for fld in ("access", "api_type", "official_mcp"):
        x = out.get(fld)
        if isinstance(x, dict):
            res[fld] = {"value": norm_value(fld, x.get("value")), "cite": x.get("cite") or []}
        else:
            res[fld] = {"value": norm_value(fld, x), "cite": []}
    return res


def flat(res: dict) -> dict:
    """{auth: sorted list, access: str, api_type: str, official_mcp: str, verdict: str}."""
    auth = sorted({a["value"] for a in res.get("auth", [])})
    access = res.get("access", {}).get("value")
    api_type = res.get("api_type", {}).get("value")
    mcp = res.get("official_mcp", {}).get("value") or "no"
    if access == "no_api":
        api_type = "none"
    return {"auth": auth, "access": access, "api_type": api_type, "official_mcp": mcp, "verdict": verdict_for(access, api_type)}


class Researcher:
    def __init__(self, fetcher: Fetcher, llm: LLM, render: bool = True):
        self.f = fetcher
        self.llm = llm
        self.render = render

    async def find_docs(self, app: dict) -> tuple[Page | None, list[Page]]:
        roots = candidate_roots(app["hint"])
        pages = await asyncio.gather(*(self.f.get(u) for u in roots))
        docs = [p for p in pages if looks_like_docs(p)]
        docs.sort(key=lambda p: (-(p.url == roots[0] and len(roots) == 1),
                                 -len(re.findall(r"\bapi\b|authenticat|endpoint", p.text[:20000], re.I))))
        # prefer an explicit docs host when it is API-heavy
        best = docs[0] if docs else next((p for p in pages if p.ok), None)
        return best, [p for p in pages if p.ok]

    async def run(self, app: dict) -> dict:
        trace: list[str] = []
        root, alive = await self.find_docs(app)
        if root is None:
            trace.append("no docs host answered over plain HTTP")
        else:
            trace.append(f"docs root: {root.final_url or root.url}")
        links = await site_index(self.f, root) if root else []
        read: dict[str, Page] = {}
        if root:
            read[(root.final_url or root.url).rstrip("/")] = root
        # Round 1: two best pages per question
        r1 = []
        for fld in FIELDS:
            r1 += rank(links, fld, set(read), 2)
        if root:
            o = urlparse(root.final_url or root.url)
            origin = f"{o.scheme}://{o.netloc}"
            base = (root.final_url or root.url).rstrip("/")
            r1 += [origin + p for p in ("/docs/api/authentication", "/docs/authentication", "/authentication",
                                        "/docs/api/overview", "/docs/getting-started")]
            if base != origin:
                more = await self.f.get(base + "/llms.txt")
                if 200 <= more.status < 300 and "<html" not in more.text[:200].lower():
                    links += [(u, a) for u, a in more.links if same_site(u, base)]
                    for fld in FIELDS:
                        r1 = rank(links, fld, set(read), 2) + r1
        r1 = [u for u in dict.fromkeys(r1) if u.rstrip("/") not in read][:12]
        for p in await asyncio.gather(*(self.f.get(u) for u in r1)):
            if p.ok:
                read[(p.final_url or p.url).rstrip("/")] = p
        trace.append(f"round 1 read {len(read)} pages")

        # ---------- PASS 1: naive LLM over page heads ----------
        heads = []
        for i, p in enumerate(list(read.values())[:5]):
            heads.append(f"S{i + 1} [{p.final_url or p.url}] {p.title} :: {p.text[:1200].replace(chr(10), ' ')}")
        p1_prompt = f"App: {app['name']} (category: {app['category']}; hint: {app['hint']} {app['hint_note']})\nEvidence:\n" + \
            ("\n".join(heads) if heads else "(no pages could be read)")
        p1_raw = await asyncio.to_thread(call_llm, self.llm, p1_prompt)
        pass1 = flat(read_llm(p1_raw))

        # ---------- LOOPS ----------
        signals: list[Signal] = [s for p in read.values() for s in extract(p)]
        best = summarize(signals)

        # L2 follow-up crawl for weak fields
        weak = [fld for fld in FIELDS if max(best.get(fld, {}).values(), default=0) < 3]
        follow = []
        for fld in weak:
            follow += rank(links, fld, set(read), 3)
        if root:
            o = urlparse(root.final_url or root.url)
            origin = f"{o.scheme}://{o.netloc}"
            probes = {"auth": ["/docs/authentication", "/authentication", "/docs/api/authentication", "/reference/authentication"],
                      "access": ["/pricing"], "official_mcp": ["/docs/mcp", "/mcp", "/docs/ai/mcp"]}
            for fld in weak:
                follow += [origin + p for p in probes.get(fld, [])]
            if "access" in weak:
                h = urlparse(hint_url(app["hint"]))
                follow.append(f"https://{h.netloc}/pricing")
        follow = [u for u in dict.fromkeys(follow) if u.rstrip("/") not in read][:12]
        fl_pages = await asyncio.gather(*(self.f.get(u) for u in follow))
        new = [p for p in fl_pages if p.ok]
        for p in new:
            read[(p.final_url or p.url).rstrip("/")] = p
        trace.append(f"L2 follow-up crawl for {weak or 'nothing'}: +{len(new)} pages")

        # L3 headless render for thin / blocked pages
        rendered = 0
        if self.render:
            thin = [u for u in [hint_url(app["hint"])] + ([root.url] if root else [])
                    if u.rstrip("/") not in read or len(read[u.rstrip('/')].text) < 1500]
            thin += [p.url for p in fl_pages if p.status in (403, 429) or (200 <= p.status < 300 and len(p.text) < 800)][:3]
            if root is None or len(read) <= 2:
                thin += candidate_roots(app["hint"])[1:4]
            for u in list(dict.fromkeys(thin))[:5]:
                rp = await self.f.render(u)
                if rp.ok:
                    read[(rp.final_url or rp.url).rstrip("/") + "#rendered"] = rp
                    rendered += 1
                    if root is None and looks_like_docs(rp):
                        root = rp
            trace.append(f"L3 browser render: {rendered} pages")

        signals = [s for p in read.values() for s in extract(p)]

        # L4 MCP registry
        reg, community = await mcp_registry(self.f, app, (root.final_url or root.url) if root else hint_url(app["hint"]))
        signals += reg
        trace.append(f"L4 MCP registry: {len(reg)} vendor-owned, {community} community servers")

        # MCP mentions must be on the vendor's own site to count
        # A docs page about "our MCP server" only counts for this app when it is on the app's own
        # domain, or (for docs hosted by a parent company, e.g. SendGrid on twilio.com) names the app.
        slug = re.sub(r"[^a-z0-9]", "", app["name"].lower().split("(")[0])

        hint_is_github = urlparse(hint_url(app["hint"])).netloc.lower() in ("github.com", "www.github.com")

        def mcp_ok(s: Signal) -> bool:
            if s.field != "official_mcp" or "registry.modelcontextprotocol" in s.url:
                return True
            if hint_is_github:  # github.com page chrome mentions GitHub's own MCP products
                return False
            if same_site(s.url, hint_url(app["hint"])):
                return True
            return bool(root and same_site(s.url, root.final_url or root.url)) and slug in re.sub(r"[^a-z0-9]", "", (s.url + s.quote).lower())

        signals = [s for s in signals if mcp_ok(s)]

        # Final LLM run on targeted, numbered evidence
        block, ids = snippet_block(signals, root)
        f_prompt = f"App: {app['name']} (category: {app['category']}; hint: {app['hint']} {app['hint_note']})\nEvidence snippets:\n{block}"
        f_raw = await asyncio.to_thread(call_llm, self.llm, f_prompt)
        fres = read_llm(f_raw)

        gh = await github_org_mcp(app)
        signals += gh
        trace.append(f"L4b GitHub org check: {len(gh)} vendor MCP repo(s)")

        final, evidence, flags = self.reconcile(app, fres, ids, signals, root)
        trace.append(f"L5/L6 grounding + reconciliation: {len(flags)} flags")

        return {
            "id": app["id"], "name": app["name"], "category": app["category"], "hint": app["hint"],
            "docs_url": (root.final_url or root.url) if root else None,
            "one_liner": fres.get("one_liner") or (root.title if root else ""),
            "blocker": fres.get("blocker") or "",
            "api_breadth": fres.get("api_breadth"),
            "pass1": pass1,
            "final": final,
            "evidence": evidence,
            "flags": flags,
            "community_mcp_servers": community,
            "pages_read": len(read),
            "pages_rendered": rendered,
            "trace": trace,
            "llm": self.llm.name,
        }

    # ------------------------------------------------------------------
    def reconcile(self, app: dict, fres: dict, ids: dict[str, Signal], signals: list[Signal], root: Page | None):
        flags: list[dict] = []
        evidence: dict[str, list[dict]] = {}
        sig = summarize(signals)

        def cited(field: str, value: str, cites: list) -> list[Signal]:
            got = [ids[c] for c in cites if isinstance(c, str) and c in ids]
            ok = [s for s in got if s.field == field and s.value == value and supports(field, value, s.quote)]
            if ok:
                return ok
            # Small models often cite the wrong id. Re-ground: accept the claim only if a
            # snippet the model was shown states this exact value clearly (strength >= 2).
            pool = list(ids.values()) + [s for s in signals if s.strength >= 3]
            alt = [s for s in pool if s.field == field and s.value == value and s.strength >= 2
                   and supports(field, value, s.quote)]
            if alt and value:
                flags.append({"field": field, "kind": "recited", "value": value,
                              "detail": "LLM cited the wrong snippet; claim re-grounded on a snippet that states it"})
            return alt[:2]

        def best_quotes(field: str, value: str, k: int = 2) -> list[Signal]:
            c = [s for s in signals if s.field == field and s.value == value]
            c.sort(key=lambda s: -s.strength)
            return c[:k]

        # ---- auth: grounded LLM values + explicit rule hits on auth pages
        auth_final: dict[str, list[Signal]] = {}
        auth_evidence = sum(1 for s in signals if s.field == "auth" and s.strength >= 3)
        unverified: list[str] = []
        for a in fres.get("auth", []):
            g = cited("auth", a["value"], a["cite"])
            if g:
                auth_final[a["value"]] = g
            elif auth_evidence >= 3:
                # The pages do talk about auth and none of them says this: drop the claim.
                flags.append({"field": "auth", "kind": "ungrounded", "value": a["value"], "detail": "LLM claim contradicted by silence of well-covered auth docs; dropped"})
            else:
                # Thin evidence: keep the model's answer but never trust it silently.
                unverified.append(a["value"])
        for v in unverified:
            auth_final.setdefault(v, [])
            flags.append({"field": "auth", "kind": "unverified", "value": v, "detail": "kept from the LLM, but no quote in the pages read says it; needs a human look"})
        authish = re.compile(r"auth|oauth|token|key|credential|getting-started|quickstart|security", re.I)
        for v in ("oauth2", "api_key", "basic"):
            if v not in auth_final:
                strong = [s for s in signals if s.field == "auth" and s.value == v and s.strength >= 3 and authish.search(s.url)]
                if strong:
                    auth_final[v] = strong[:2]
                    flags.append({"field": "auth", "kind": "rule_added", "value": v, "detail": "explicit statement on an auth page; LLM missed it"})

        # ---- api_type from rules first (they are precise), LLM as tie-break
        rest = sig.get("api_type", {}).get("rest", 0)
        gql = sig.get("api_type", {}).get("graphql", 0)
        llm_api = fres.get("api_type", {}).get("value")
        if rest >= 2 and gql >= 3:
            api_type = "both"
        elif gql >= 3 and rest < 2:
            api_type = "graphql"
        elif rest >= 2:
            api_type = "rest"
        else:
            api_type = llm_api if llm_api in API_TYPES else None

        # ---- access: grounded LLM value vs rule signals
        acc = fres.get("access", {})
        llm_acc, acc_cites = acc.get("value"), cited("access", acc.get("value") or "", acc.get("cite") or [])
        a_sig = sig.get("access", {})
        is_repo = urlparse(hint_url(app["hint"])).netloc.lower() in ("github.com", "www.github.com")
        # A GitHub repo is a network API only if its README documents endpoints; GitHub's own
        # page chrome ("REST API", "GraphQL") does not count.
        repo_endpoints = any(s.field == "api_type" and re.search(r"(GET|POST|PUT|DELETE) /|localhost:\d+", s.quote)
                             for s in signals)
        if is_repo and not repo_endpoints:
            access = "no_api"
            evidence["access"] = [s.to_json() for s in best_quotes("access", "no_api")]
        elif llm_acc and acc_cites:
            access = llm_acc
            evidence["access"] = [s.to_json() for s in acc_cites[:2]]
            gates = [g for g in ("paid_plan", "partner", "approval") if a_sig.get(g, 0) >= 3 and g != access]
            hard = [g for g in gates if g in ("paid_plan", "partner")]
            if access == "self_serve" and hard:
                # An explicit, API-specific restriction ("Only stores on paid plans can access API")
                # outranks generic sign-up copy. Still flagged so a human confirms it.
                flags.append({"field": "access", "kind": "conflict", "value": hard[0],
                              "detail": f"LLM said self_serve; docs explicitly state {hard[0]}, which wins"})
                access = hard[0]
                evidence["access"] = [s.to_json() for s in best_quotes("access", access)]
            elif access == "self_serve" and gates:
                flags.append({"field": "access", "kind": "conflict", "value": access,
                              "detail": f"LLM says self_serve but docs also mention {', '.join(gates)} (often only for public apps)", "alt": gates})
        else:
            if llm_acc:
                flags.append({"field": "access", "kind": "ungrounded", "value": llm_acc, "detail": "LLM access claim had no supporting quote"})
            order = [("paid_plan", 3), ("approval", 3), ("partner", 3), ("self_serve", 3), ("self_serve", 2)]
            access = next((v for v, st in order if a_sig.get(v, 0) >= st), None)
            if llm_acc and (access is None or (access == "self_serve" and llm_acc != "self_serve" and a_sig.get("self_serve", 0) < 3)):
                access = llm_acc
            if access:
                evidence["access"] = [s.to_json() for s in best_quotes("access", access)]
                flags.append({"field": "access", "kind": "rule_only", "value": access, "detail": "decided by rules alone; needs a human look"})
            else:
                flags.append({"field": "access", "kind": "missing", "value": None, "detail": "no evidence about the access gate"})
        if access == "no_api":
            api_type = "none"
            auth_final = {}

        # ---- MCP: only vendor-site statements or vendor-owned registry entries
        mcp_sig = [s for s in signals if s.field == "official_mcp" and s.strength >= 3]
        llm_mcp = fres.get("official_mcp", {})
        mcp_cited = cited("official_mcp", "yes", llm_mcp.get("cite") or []) if llm_mcp.get("value") == "yes" else []
        mcp = "yes" if (mcp_sig or mcp_cited) else "no"
        if llm_mcp.get("value") == "yes" and not (mcp_sig or mcp_cited):
            flags.append({"field": "official_mcp", "kind": "ungrounded", "value": "yes", "detail": "LLM said yes without vendor evidence"})
        if mcp == "yes":
            evidence["official_mcp"] = [s.to_json() for s in (mcp_cited or mcp_sig)[:2]]

        evidence["auth"] = [s.to_json() for v in auth_final.values() for s in v[:1]]
        evidence["api_type"] = [s.to_json() for s in best_quotes("api_type", "graphql" if api_type == "graphql" else "rest")[:1]]
        if not auth_final and access != "no_api":
            flags.append({"field": "auth", "kind": "missing", "value": None, "detail": "no auth method found in the pages read"})
        if api_type is None:
            flags.append({"field": "api_type", "kind": "missing", "value": None, "detail": "no API style found"})

        final = {"auth": sorted(auth_final), "access": access, "api_type": api_type, "official_mcp": mcp,
                 "verdict": verdict_for(access, api_type)}
        return final, evidence, flags
