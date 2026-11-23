"""Deterministic evidence extraction.

Each rule is a regex tied to a (field, value). Running the rules over a page
yields `Signal`s: the URL plus a quote window around the match. These do two
jobs: they are the evidence snippets the LLM reads, and they are the
independent second opinion the verifier compares the LLM against.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .fetch import Page


@dataclass(frozen=True)
class Signal:
    field: str
    value: str
    url: str
    quote: str
    strength: int  # 1 = weak hint, 2 = clear statement, 3 = explicit, API-specific statement

    def to_json(self) -> dict:
        return self.__dict__


I = re.IGNORECASE

# (field, value, strength, pattern)
RULES: list[tuple[str, str, int, re.Pattern]] = [
    # ---- auth
    ("auth", "oauth2", 3, re.compile(r"oauth\s*2(\.0)?|oauth2", I)),
    ("auth", "oauth2", 2, re.compile(r"authorization[_ ]code (grant|flow)|client[_ ]credentials (grant|flow)|/oauth2?/(authorize|token)|\boauth (app|flow|client|integration)s?\b", I)),
    ("auth", "api_key", 3, re.compile(r"\bapi[\s_-]?keys?\b|\bapi[\s_-]?tokens?\b|personal access tokens?|private app (access )?tokens?|\bbot tokens?\b|secret keys?\b|access tokens? (from|in) (your|the) (dashboard|settings|account)", I)),
    ("auth", "api_key", 2, re.compile(r"x-api-key|authorization: bearer\s+(your|<|\{|\$)|generate (a|an|your) (new )?(token|key)", I)),
    ("auth", "basic", 3, re.compile(r"(http )?basic auth(entication)?\b|authorization: basic|base64[- ]encoded? (string|username|credentials)", I)),
    ("auth", "other", 2, re.compile(r"\bhmac\b|signature version 4|\bsigv4\b|service accounts?\b|mutual tls|\bmtls\b|json web tokens?|\bjwt\b", I)),
    # ---- access gate
    ("access", "self_serve", 3, re.compile(r"free (developer )?(account|plan|tier|trial|sandbox)|sign up for (a )?free|no credit card|start(ing)? for free|free to (use|get started)|developer (sandbox|test) accounts? (is|are) free", I)),
    ("access", "self_serve", 2, re.compile(r"(create|generate|get) (an? |your )?(api key|api token|access token|personal access token)|developer (portal|console|dashboard)|create (an? )?(app|integration) in", I)),
    ("access", "paid_plan", 3, re.compile(r"(api|integration)s?( access)? (is |are )?(only )?(available|included) (on|in|for|with) (all )?(paid|pro|premium|business|growth|scale|advanced|professional|plus)\b|requires? an? (paid|pro|premium|business) (plan|subscription|account)|only (stores|accounts|customers|users) on (a )?paid plans?|upgrade (your plan )?to (access|use|unlock) the api", I)),
    ("access", "approval", 3, re.compile(r"app review|submit (your )?app for review|(apply|application) for (api )?access|request (api )?access|developer (application|registration) (is |must be )?(approved|reviewed)|waitlist|must be approved|approval process|approved (sp-api )?(application|app|developer)", I)),
    ("access", "partner", 3, re.compile(r"(api|integration)s?[^.\n]{0,60}(enterprise (plan|customers|tier|workspaces)|contact (sales|us|your account manager))|(enterprise|account manager)[^.\n]{0,60}\bapi\b|partner(ship)? (program|agreement)[^.\n]{0,40}(api|access)|become a (technology |integration )?partner", I)),
    ("access", "partner", 2, re.compile(r"contact (sales|your account manager)|talk to sales|request a demo", I)),
    # ---- API style
    ("api_type", "graphql", 3, re.compile(r"graphql (api|endpoint|schema|explorer)|\bgraphql\b[^.\n]{0,40}(query|mutation)", I)),
    ("api_type", "rest", 3, re.compile(r"\brest(ful)? api\b|\brest(ful)?\b[^.\n]{0,30}(endpoint|interface|semantics|principles)|(GET|POST|PUT|PATCH|DELETE) /[a-z{]", I)),
    ("api_type", "rest", 2, re.compile(r"https://api\.[a-z0-9.-]+/|openapi|swagger|json over http|api reference", I)),
    # ---- MCP
    ("official_mcp", "yes", 3, re.compile(r"\b(our|official|remote|hosted|local)\b[^.\n]{0,40}\bmcp server\b|\bmcp server\b[^.\n]{0,60}(url|endpoint|connect|install)|https://mcp\.[a-z0-9.-]+|model context protocol", I)),
    ("official_mcp", "yes", 2, re.compile(r"\bmcp\b", 0)),
    # ---- no public API (CLI / library only)
    ("access", "no_api", 2, re.compile(r"command[- ]line (interface|tool)|\bcli\b for|pip(x)? install|npm install -g", I)),
]


def window(text: str, start: int, end: int, size: int = 160) -> str:
    a = max(0, start - size)
    b = min(len(text), end + size)
    s = text[a:b].replace("\n", " ")
    return re.sub(r"\s+", " ", s).strip()


NOISE_PAGES = re.compile(r"/(changelog|blog|release-notes|releases|news|community|forum|press|careers|customers|stories)(/|$|\.)", I)
NEGATION = re.compile(r"reject|deprecat|no longer|not (be )?supported|retired|sunset|removed|discontinu|legacy", I)


def extract(page: Page, max_per_rule: int = 3) -> list[Signal]:
    """Quotes for every rule hit. Changelog/blog pages are skipped (they describe other
    products or old behaviour), and a quote that negates the claim ("Basic auth is
    rejected", "API keys are deprecated") is demoted to a weak hint."""
    if not page.text:
        return []
    url = page.final_url or page.url
    if NOISE_PAGES.search(url):
        return []
    out: list[Signal] = []
    for field, value, strength, pat in RULES:
        n = 0
        for m in pat.finditer(page.text):
            q = window(page.text, m.start(), m.end())
            st = 1 if (field in ("auth", "access") and NEGATION.search(window(page.text, m.start(), m.end(), 80))) else strength
            out.append(Signal(field, value, url, q, st))
            n += 1
            if n >= max_per_rule:
                break
    return out


def summarize(signals: list[Signal]) -> dict[str, dict[str, int]]:
    """field -> value -> best strength seen."""
    best: dict[str, dict[str, int]] = {}
    for s in signals:
        best.setdefault(s.field, {})
        best[s.field][s.value] = max(best[s.field].get(s.value, 0), s.strength)
    return best


# Words that must appear in a quote for it to count as support for a value.
SUPPORT = {
    ("auth", "oauth2"): re.compile(r"oauth", I),
    ("auth", "api_key"): re.compile(r"api[\s_-]?key|token|secret|credential", I),
    ("auth", "basic"): re.compile(r"basic", I),
    ("auth", "other"): re.compile(r"hmac|sig|jwt|json web|service account|tls|saml|session", I),
    ("access", "self_serve"): re.compile(r"free|sign up|trial|sandbox|create|generate|self|no credit", I),
    ("access", "paid_plan"): re.compile(r"paid|plan|subscription|upgrade|pro\b|premium|business|tier", I),
    ("access", "approval"): re.compile(r"review|approv|apply|application|request|waitlist|registration", I),
    ("access", "partner"): re.compile(r"enterprise|sales|partner|account manager|contact|demo", I),
    ("access", "no_api"): re.compile(r"cli|command|library|install|no (public )?api|package", I),
    ("api_type", "rest"): re.compile(r"rest|GET |POST |https://api|endpoint|http", I),
    ("api_type", "graphql"): re.compile(r"graphql", I),
    ("api_type", "both"): re.compile(r"graphql|rest", I),
    ("api_type", "none"): re.compile(r"cli|command|library|no (public )?api", I),
    ("official_mcp", "yes"): re.compile(r"\bmcp\b|model context protocol", I),
}


def supports(field: str, value: str, quote: str) -> bool:
    pat = SUPPORT.get((field, value))
    return bool(pat and pat.search(quote))
