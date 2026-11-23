"""HTTP fetching with a disk cache, HTML-to-text, and a headless-browser fallback.

Every page the agent reads goes through `get()`, so every claim it makes can be
traced back to a cached copy of the page under runs/cache/.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

CACHE = Path(__file__).resolve().parent.parent / "runs" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 app-research-agent/0.1"
)


@dataclass
class Page:
    url: str
    status: int
    final_url: str = ""
    title: str = ""
    text: str = ""
    links: list[tuple[str, str]] = field(default_factory=list)  # (url, anchor text)
    rendered: bool = False  # True when the text came from the headless browser
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and len(self.text) > 200

    def to_json(self) -> dict:
        return self.__dict__

    @classmethod
    def from_json(cls, d: dict) -> "Page":
        d = dict(d)
        d["links"] = [tuple(x) for x in d.get("links", [])]
        return cls(**d)


def _key(url: str, rendered: bool) -> Path:
    h = hashlib.sha1(f"{'R:' if rendered else ''}{url}".encode()).hexdigest()[:20]
    return CACHE / f"{h}.json"


def html_to_page(url: str, status: int, final_url: str, html: str) -> Page:
    tree = HTMLParser(html)
    title = (tree.css_first("title").text(strip=True) if tree.css_first("title") else "")[:200]
    links: list[tuple[str, str]] = []
    base = final_url or url
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        if href.startswith(("#", "mailto:", "javascript:", "tel:")):
            continue
        links.append((urljoin(base, href).split("#")[0], a.text(strip=True)[:120]))
    for tag in tree.css("script, style, noscript, svg, iframe, template"):
        tag.decompose()
    body = tree.body or tree.root
    text = body.text(separator="\n") if body else ""
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text).strip()
    return Page(url=url, status=status, final_url=final_url, title=title, text=text[:200_000], links=links)


def _plain_to_page(url: str, status: int, final_url: str, body: str) -> Page:
    links = [(m.group(0), "") for m in re.finditer(r"https?://[^\s)\]>\"']+", body)]
    # markdown links: [title](url)
    links += [(urljoin(final_url or url, m.group(2)), m.group(1)) for m in re.finditer(r"\[([^\]]{1,120})\]\(([^)\s]+)\)", body)]
    return Page(url=url, status=status, final_url=final_url, text=body[:200_000], links=links)


class Fetcher:
    """Async fetcher. One per run; limits concurrency per host and overall."""

    def __init__(self, concurrency: int = 16, timeout: float = 20.0):
        self.sem = asyncio.Semaphore(concurrency)
        self.client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,text/plain,application/xml;q=0.9,*/*;q=0.8",
                     "Accept-Language": "en-US,en;q=0.9"},
            follow_redirects=True,
            timeout=timeout,
            http2=False,
        )
        self.host_sems: dict[str, asyncio.Semaphore] = {}
        self.stats = {"fetched": 0, "cached": 0, "rendered": 0, "failed": 0}
        self._browser = None
        self._pw = None
        self._render_lock = asyncio.Semaphore(3)

    async def close(self):
        await self.client.aclose()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    def _host_sem(self, url: str) -> asyncio.Semaphore:
        host = urlparse(url).netloc
        return self.host_sems.setdefault(host, asyncio.Semaphore(3))

    async def get(self, url: str, use_cache: bool = True) -> Page:
        path = _key(url, False)
        if use_cache and path.exists():
            self.stats["cached"] += 1
            return Page.from_json(json.loads(path.read_text(encoding="utf-8")))
        async with self.sem, self._host_sem(url):
            try:
                r = await self.client.get(url)
                ctype = r.headers.get("content-type", "")
                body = r.text if ("text" in ctype or "json" in ctype or "xml" in ctype or not ctype) else ""
                if "html" in ctype or body.lstrip().lower().startswith(("<!doctype html", "<html")):
                    page = html_to_page(url, r.status_code, str(r.url), body)
                else:
                    page = _plain_to_page(url, r.status_code, str(r.url), body)
                self.stats["fetched"] += 1
            except Exception as e:  # network errors are data, not crashes
                page = Page(url=url, status=0, error=f"{type(e).__name__}: {e}"[:300])
                self.stats["failed"] += 1
        path.write_text(json.dumps(page.to_json()), encoding="utf-8")
        return page

    async def render(self, url: str, use_cache: bool = True) -> Page:
        """Load the page in headless Edge/Chromium and read the rendered DOM.

        Used as a verification loop for JS-rendered docs (SPA docs portals) and for
        sites that refuse plain HTTP clients.
        """
        path = _key(url, True)
        if use_cache and path.exists():
            return Page.from_json(json.loads(path.read_text(encoding="utf-8")))
        async with self._render_lock:
            try:
                if self._browser is None:
                    from playwright.async_api import async_playwright

                    self._pw = await async_playwright().start()
                    try:
                        self._browser = await self._pw.chromium.launch(channel="msedge", headless=True)
                    except Exception:
                        self._browser = await self._pw.chromium.launch(headless=True)
                ctx = await self._browser.new_context(user_agent=UA.replace(" app-research-agent/0.1", ""))
                pg = await ctx.new_page()
                resp = await pg.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    await pg.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                html = await pg.content()
                status = resp.status if resp else 0
                final = pg.url
                await ctx.close()
                page = html_to_page(url, status, final, html)
                page.rendered = True
                self.stats["rendered"] += 1
            except Exception as e:
                page = Page(url=url, status=0, rendered=True, error=f"{type(e).__name__}: {e}"[:300])
        path.write_text(json.dumps(page.to_json()), encoding="utf-8")
        return page


def same_site(a: str, b: str) -> bool:
    """True when two URLs share a registrable domain (rough eTLD+1)."""

    def reg(u: str) -> str:
        host = urlparse(u).netloc.lower().split(":")[0]
        parts = host.split(".")
        if len(parts) >= 3 and parts[-2] in {"co", "com", "org", "net"} and len(parts[-1]) == 2:
            return ".".join(parts[-3:])
        return ".".join(parts[-2:])

    return reg(a) == reg(b)


def now() -> float:
    return time.time()
