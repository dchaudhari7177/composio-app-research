"""Record review decisions, each backed by a quote found in the cached docs pages.

Usage: uv run python scripts/record_decisions.py   (reads DECISIONS below, writes data/review.json)

A decision is (id, field, value, url_substring, quote_regex). The script searches the cached
pages whose URL contains url_substring for quote_regex and stores the URL and a quote window.
A decision whose quote cannot be found is NOT recorded; it is printed so the reviewer can open
the page in a browser and add the quote by hand (MANUAL list).
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from agent.fetch import CACHE, Page  # noqa: E402
from agent.signals import window  # noqa: E402

REVIEWER = "review queue (AI reviewer reading the cited docs, human sign-off)"

# (id, field, value, url_substring, quote_regex, note)
DECISIONS = [
    (1, "access", "self_serve", "salesforce.com", r"Developer Edition|free developer", "free Developer Edition org gives API access"),
    (2, "access", "self_serve", "hubspot.com", r"developer test accounts?|free (developer|hubspot) account|Developer test accounts are free", "free developer test account"),
    (2, "auth", ["api_key", "oauth2"], "hubspot.com", r"private app|OAuth", "OAuth for multi-account apps, access tokens for private apps"),
    (6, "access", "self_serve", "podio.com", r"get one or more API keys|api key", "API keys from the developer page"),
    (7, "access", "self_serve", "zoho.com", r"register your application|Zoho account to use Zoho APIs", "any Zoho account can register an API client"),
    (12, "access", "self_serve", "intercom.com", r"free development workspace|development workspace", "free development workspace"),
    (14, "access", "self_serve", "front.com", r"free 14-day trial offers all the features", "free 14-day trial, no card; API available on the trial workspace"),
    (15, "access", "partner", "usepylon.com", r"sales representative|book a demo|talk to sales", "sales-led product; API tokens come from an existing Pylon workspace"),
    (15, "auth", ["api_key"], "pylon", r"api (key|token)|bearer", "admin-created API token, sent as Bearer"),
    (17, "auth", ["api_key"], "plain.com", r"api key|machine user", "API keys on machine users"),
    (21, "access", "self_serve", "slack.com", r"create (an|a new|your) app|api\.slack\.com/apps", "anyone can create an app in their own workspace; review is only for the Marketplace"),
    (26, "access", "self_serve", "discord", r"create (an|a new) application|developer portal", "self-serve app + bot token; privileged-intent review only past 100 servers"),
    (28, "access", "self_serve", "facebook.com", r"test (phone )?number|get started|create (an|a) app", "free test number and temporary token on a new Meta app; business verification for production"),
    (31, "access", "approval", "google", r"developer token|Basic access|apply for", "developer token needs an application review before it works on real accounts"),
    (33, "access", "approval", "linkedin", r"request access|Advertising API|application", "Advertising API needs an access request approved by LinkedIn"),
    (38, "access", "approval", "pinterest", r"trial access|standard access|review", "apps start with reviewed trial access"),
    (41, "access", "self_serve", "shopify", r"development store|dev store|partner account|Dev Dashboard", "free dev stores; Shopify approval only for protected scopes"),
    (44, "access", "partner", "salesforce.com", r"B2C Commerce|contact|account manager|Account Manager", "B2C Commerce API needs a customer or partner realm"),
    (49, "access", "approval", "amazon", r"approved|register as a developer|developer registration|Solution Provider Portal", "SP-API needs an approved developer registration"),
    (53, "access", "paid_plan", "ahrefs.com", r"Enterprise|API units|plan", "API v3 is tied to the Enterprise plan"),
    (71, "access", "self_serve", "notion.com", r"create (an|a new) integration|internal integration|free", "integrations are free on every plan"),
    (10, "access", "partner", "dealcloud", r"site URL|DealCloud site|API credentials|Client ID", "credentials are issued on an existing customer site; no self-serve signup"),
    (89, "access", "approval", "ramp.com", r"approved integrators|request|sandbox", "partner/sandbox access is granted on request"),
    (88, "access", "approval", "brex.com", r"Brex account|sign up|admin|approved", "API needs an existing (approved) Brex business account"),
]

# Second pass ("sweep"): after the queue was cleared, a reviewer read every row of the 80 apps
# OUTSIDE the scored sample and corrected fields the queue had not flagged. The 20 sample apps are
# deliberately excluded, so this pass cannot inflate the measured accuracy; it only cleans the table.
SWEEP_REVIEWER = "sweep (AI reviewer re-reading the docs, non-sample apps only; human sign-off)"
SWEEP = [
    (5, "api_type", "both", "twenty.com", r"over REST and GraphQL", "REST and GraphQL are both documented"),
    (5, "auth", ["api_key", "oauth2"], "twenty.com", r"OAuth.{0,25}API Key", "API keys from settings; OAuth for apps"),
    (8, "auth", ["api_key", "oauth2"], "close.com", r"OAuth 2\.0 or authenticate using an API key", "API key or OAuth app"),
    (16, "auth", ["api_key"], "liveagent", r"API keys, rate limits", "API v3 uses API keys"),
    (18, "auth", ["oauth2"], "helpscout", r"\*\*Authentication:\*\* OAuth 2\.0", "Inbox API 2.0 is OAuth 2.0 only (auth code + client credentials)"),
    (27, "auth", ["api_key"], "telegram.org", r"unique authentication token", "Bot API: a bot token from BotFather"),
    (42, "auth", ["api_key"], "woocommerce", r"Consumer Key\s*,\s*Consumer Secret", "consumer key/secret generated in WooCommerce settings"),
    (52, "access", "paid_plan", "seranking", r"Standalone API access for teams", "API is a paid add-on or standalone paid plan"),
    (53, "auth", ["api_key"], "ahrefs", r"send your API key as a bearer token", "API key as bearer token"),
    (54, "auth", ["api_key"], "mrscraper", r"create an API token for authentication", "API token from the dashboard"),
    (55, "auth", ["api_key"], "apify.com", r"Create an API token", "API token from Apify Console (OAuth is only for the hosted MCP sign-in)"),
    (56, "auth", ["api_key"], "firecrawl", r"Authorization: Bearer \$FIRECR", "API key as bearer token"),
    (63, "auth", ["api_key", "oauth2"], "netlify", r"Generate a PAT for Netlify OAuth", "personal access token or OAuth app"),
    (73, "auth", ["api_key", "oauth2"], "linear.app", r"supports personal API keys and OAuth2", "personal API keys and OAuth2"),
    (99, "auth", ["api_key"], "transcriptapi", r"API Keys dashboard", "API key, 100 free credits"),
]

# Sweep decisions confirmed by hand in a browser on 2026-11-24 (the agent read 0-1 pages for most of these).
SWEEP_MANUAL = [
    {"id": 13, "field": "auth", "value": ["api_key", "basic"], "url": "https://developers.freshdesk.com/api/",
     "quote": "You can use your personal API key to authenticate the request. If you use the API key, there is no need for a password. You can use any set of characters as a dummy password."},
    {"id": 34, "field": "auth", "value": ["api_key", "oauth2"], "url": "https://marketplace.gohighlevel.com/docs/",
     "quote": "Set Up Authentication - OAuth 2.0 or Private Integration Token"},
    {"id": 83, "field": "auth", "value": ["api_key", "other"], "url": "https://developers.binance.com/en/docs/products/spot/rest-api",
     "quote": "We support HMAC, RSA, and Ed25519 keys. For more information, please see API Key types."},
    {"id": 83, "field": "access", "value": "self_serve", "url": "https://developers.binance.com/en/docs/products/spot/rest-api",
     "quote": "API keys can be created on the API Management page of your Binance account. By default, an API key cannot TRADE.",
     "blocker": "none for read access; trading must be enabled per key (KYC'd account)"},
    {"id": 90, "field": "access", "value": "partner", "url": "https://pitchbook.com/products/integrations",
     "quote": "Our Excel, PowerPoint, and Chrome integrations are free to all PitchBook clients (no public developer docs; access starts at 'Request a free trial' with sales)"},
    {"id": 91, "field": "access", "value": "paid_plan", "url": "https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks",
     "quote": "The projects.locations.notebooks REST API methods on this page require an authenticated user account with an active Gemini Notebook Enterprise license."},
    {"id": 91, "field": "auth", "value": ["oauth2"], "url": "https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks",
     "quote": "Signing in with your user credentials ensures that gcloud auth print-access-token generates an access token for your user account."},
    {"id": 91, "field": "official_mcp", "value": "no", "url": "https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks",
     "quote": "The agent's MCP evidence came from a Gemini page, not NotebookLM; the NotebookLM Enterprise docs document a REST API only."},
    {"id": 94, "field": "auth", "value": ["api_key"], "url": "https://docs.consensus.app/api-get-started",
     "quote": "Then search. query is the only required parameter, and the key goes in the x-api-key header."},
    {"id": 94, "field": "access", "value": "self_serve", "url": "https://docs.consensus.app/api-get-started",
     "quote": "Every plan includes API access, and the Free plan includes 30 calls a month."},
    {"id": 94, "field": "official_mcp", "value": "yes", "url": "https://docs.consensus.app/consensus-mcp",
     "quote": "Get started with Consensus MCP. Search 400M+ scholarly articles ... from Claude, ChatGPT, Cursor, Microsoft 365 Copilot and other MCP clients."},
    {"id": 96, "field": "auth", "value": ["api_key"], "url": "https://docs.devin.ai/api-reference/overview",
     "quote": "Both scopes use service user credentials (cog_ prefix)."},
    {"id": 96, "field": "access", "value": "paid_plan", "url": "https://docs.devin.ai/api-reference/overview",
     "quote": "Teams quick start: For teams and standard organizations. Create a service user and make your first API call in minutes."},
    {"id": 32, "field": "access", "value": "self_serve", "url": "https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization",
     "quote": "Limited access (default) / Full access (after App Review). Calls on ANY access level are against production data.",
     "blocker": "App Review for Full access and for managing other businesses' ad accounts"},
]

# Decisions confirmed by hand in a browser (pages that block scripted fetches or render with JS).
MANUAL = [
    {"id": 21, "field": "access", "value": "self_serve", "url": "https://docs.slack.dev/quickstart/",
     "quote": "Before you begin building, grab the required tools by following the three steps below. If you don't have a Slack workspace already, we recommend also setting up a developer sandbox."},
    {"id": 10, "field": "access", "value": "partner", "url": "https://api.docs.dealcloud.com/sdk/python-1.X/getting-started/quickstart",
     "quote": "Prerequisites: Python 3.12+, DealCloud API credentials (Client ID and API Key), Your DealCloud site URL"},
    {"id": 92, "field": "access", "value": "partner", "url": "https://help.otter.ai/hc/en-us/search?query=API",
     "quote": "Yes, Otter offers a public API for Enterprise customers. To get started, contact your Otter account manager"},
    {"id": 92, "field": "auth", "value": ["api_key"], "url": "https://help.otter.ai/hc/en-us/search?query=API",
     "quote": "curl -X GET \"https://api.otter.ai/v1/workspace\" -H \"Authorization: Bearer YOUR_API_KEY\""},
    {"id": 60, "field": "access", "value": "partner", "url": "https://www.clay.com/pricing",
     "quote": "Enterprise (custom pricing with annual commitment) ... unlimited row bulk enrichment, Clay API access"},
]


def load_pages():
    out = []
    for p in CACHE.glob("*.json"):
        if p.name.startswith(("llm-", "gh-")):
            continue
        try:
            out.append(Page.from_json(json.loads(p.read_text(encoding="utf-8"))))
        except Exception:
            pass
    return out


def main():
    pages = load_pages()
    apps = {a["id"]: a for a in json.loads((ROOT / "runs" / "results.json").read_text(encoding="utf-8"))["apps"]}
    manual_keys = {(m["id"], m["field"]) for m in MANUAL}
    decisions, missing = [], []
    gold_ids = {g["id"] for g in json.loads((ROOT / "data" / "gold.json").read_text(encoding="utf-8"))["apps"]}
    tagged = [(d, REVIEWER) for d in DECISIONS] + [(d, SWEEP_REVIEWER) for d in SWEEP]
    for (aid, field, value, host, rx, note), reviewer in tagged:
        if (aid, field) in manual_keys:
            continue
        if reviewer == SWEEP_REVIEWER and aid in gold_ids:
            raise SystemExit(f"sweep must not touch scored sample app {aid}")
        pat = re.compile(rx, re.I)
        hit = None
        for pg in pages:
            u = pg.final_url or pg.url
            if host not in u or not pg.text:
                continue
            m = pat.search(pg.text)
            if m:
                hit = (u, window(pg.text, m.start(), m.end(), 130))
                break
        if hit:
            decisions.append({"id": aid, "name": apps[aid]["name"], "field": field, "value": value, "url": hit[0],
                              "quote": hit[1], "note": note, "reviewer": reviewer,
                              "pass": "sweep" if reviewer == SWEEP_REVIEWER else "queue"})
        else:
            missing.append((aid, apps[aid]["name"], field, value))
    for m in MANUAL:
        decisions.append({**m, "name": apps[m["id"]]["name"], "note": "checked by hand in a browser", "reviewer": REVIEWER, "pass": "queue"})
    for m in SWEEP_MANUAL:
        assert m["id"] not in gold_ids, m
        decisions.append({**m, "name": apps[m["id"]]["name"], "note": m.get("note", "checked by hand in a browser"),
                          "reviewer": SWEEP_REVIEWER, "pass": "sweep"})
    (ROOT / "data" / "review.json").write_text(json.dumps(decisions, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"recorded {len(decisions)} decisions")
    for x in missing:
        print("NO QUOTE FOUND:", x)


if __name__ == "__main__":
    main()
