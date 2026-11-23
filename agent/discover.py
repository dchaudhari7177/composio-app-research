"""Find the pages worth reading for an app.

Starting from the hint in the brief, the agent guesses the developer-docs host,
then ranks candidate pages per research question using three agent-friendly
sources: links on the docs home, /llms.txt (the index many docs sites now
publish for LLMs) and sitemap.xml.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlparse

from .fetch import Fetcher, Page, same_site

FIELD_KEYWORDS: dict[str, list[str]] = {
    "auth": ["authentication", "authorization", "oauth", "auth", "api-key", "api_key", "apikey", "api-keys", "access-token",
             "tokens", "credentials", "personal-access", "getting-started", "quickstart", "quick-start"],
    "access": ["pricing", "plans", "rate-limit", "limits", "partner", "apply", "access", "sandbox", "developer-account",
               "getting-started", "faq", "requirements", "register", "onboarding"],
    "api_type": ["api-reference", "reference", "graphql", "openapi", "endpoints", "rest-api", "rest", "introduction", "overview"],
    "official_mcp": ["mcp", "model-context", "ai-agents", "agents", "llm", "ai-tools"],
}

NOISE = re.compile(r"/(blog|careers|jobs|legal|privacy|terms|press|customers|events|webinars?|community|forum|status|login|signin|signup|changelog/\d)", re.I)


def hint_url(hint: str) -> str:
    return hint if hint.startswith("http") else f"https://{hint}"


def candidate_roots(hint: str) -> list[str]:
    """Docs hosts to probe for a hint like 'pipedrive.com' or 'developers.notion.com'."""
    url = hint_url(hint)
    p = urlparse(url)
    host = p.netloc.lower()
    out = [url]
    if "." not in host:  # hint is a bare word (e.g. 'paygent')
        return [f"https://{host}.com", f"https://www.{host}.com"]
    bare = host[4:] if host.startswith("www.") else host
    is_docs_host = bool(re.match(r"^(developers?|docs|api|dev|help|learn|api-docs|open)\.", bare)) or "github.com" in bare
    if not is_docs_host and p.path in ("", "/"):
        out += [f"https://developers.{bare}", f"https://developer.{bare}", f"https://docs.{bare}",
                f"https://{bare}/developers", f"https://{bare}/api", f"https://{bare}/docs", f"https://api.{bare}",
                f"https://www.{bare}/developers", f"https://www.{bare}/api"]
    elif not is_docs_host:  # domain + path, e.g. zoho.com/crm
        out += [f"https://{bare}", f"https://developers.{bare}", f"https://docs.{bare}"]
    return list(dict.fromkeys(out))


API_HINT = re.compile(r"\bapi\b|endpoint|authenticat|developer|sdk|webhook", re.I)


def looks_like_docs(page: Page) -> bool:
    if not page.ok:
        return False
    sample = page.text[:20000]
    return len(API_HINT.findall(sample)) >= 3


def score_link(url: str, anchor: str, field: str) -> int:
    u = url.lower()
    a = anchor.lower()
    s = 0
    for i, kw in enumerate(FIELD_KEYWORDS[field]):
        w = 10 - min(i, 6)
        if kw in u:
            s += w
        if kw.replace("-", " ") in a:
            s += w
    if NOISE.search(u):
        s -= 20
    if u.endswith((".png", ".jpg", ".svg", ".pdf", ".zip", ".css", ".js")):
        s -= 50
    return s


async def site_index(f: Fetcher, root: Page) -> list[tuple[str, str]]:
    """Links from the root page + /llms.txt + sitemap.xml on the docs host."""
    base = root.final_url or root.url
    p = urlparse(base)
    origin = f"{p.scheme}://{p.netloc}"
    links = list(root.links)
    extra = await asyncio.gather(f.get(origin + "/llms.txt"), f.get(origin + "/sitemap.xml"))
    for pg in extra:
        if 200 <= pg.status < 300 and pg.text and "<html" not in pg.text[:200].lower():
            links += pg.links
            # sitemap <loc> entries survive as plain text urls in _plain_to_page
    return [(u, a) for u, a in links if u.startswith("http") and same_site(u, base)]


def rank(links: list[tuple[str, str]], field: str, exclude: set[str], k: int) -> list[str]:
    seen: dict[str, int] = {}
    for u, a in links:
        u = u.rstrip("/")
        if u in exclude:
            continue
        sc = score_link(u, a, field)
        if sc > 0:
            seen[u] = max(seen.get(u, 0), sc)
    return [u for u, _ in sorted(seen.items(), key=lambda x: -x[1])[:k]]
