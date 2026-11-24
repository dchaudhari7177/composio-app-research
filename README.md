# App toolkit readiness: an agent that researches 100 SaaS apps

Composio take-home (AI Product Ops Intern). Before building a toolkit for an app you need to know how it authenticates, whether a developer can get credentials alone or has to get through a gate, what the API looks like, and whether the vendor already ships an MCP server. This repo is an agent that answers those questions for any app, with a docs URL and a quote behind every answer. It has been run on the 100 apps in the brief.

**Case study (the deliverable):** https://dchaudhari7177.github.io/composio-app-research/ (built from `site/index.html`). It shows the patterns, the 100-row table, the agent, and the verification. Machine-readable twins: [`apps.json`](site/apps.json), [`apps.csv`](site/apps.csv), [`llms.txt`](site/llms.txt).

## Headline

- **79 of 100 apps can be a toolkit today** (self-serve credentials + public API). 11 more are buildable after a paid plan or an app review, 6 need sales or a partnership, 2 have no network API, and 2 could not be settled from public docs and are left unresolved rather than guessed.
- The gate follows who the vendor sells to: dev tools, productivity and messaging are 10/10 self-serve; ad/marketplace platforms are approval-gated; sales-led data and finance tools need outreach.
- API key (75) and OAuth2 (61) dominate and 39 apps offer both. 69 vendors already ship an official MCP server.
- **12 apps Composio doesn't cover yet are build-now today** (LiveAgent, Gladly, Vonage, systeme.io, WooCommerce, Magento, Squarespace, Ecwid, Waterfall.io, Binance, iPayX, Reducto).
- Field accuracy on a 20-app hand-checked sample: **64.5% naive pass → 77.4% after the verification loops → 82.6% after human review.** All six findings are in [`data/findings.md`](data/findings.md).

## What it does

```
hint from the brief ──▶ find the docs host ──▶ rank pages per question ──▶ PASS 1 (naive LLM)
   (developers./docs./api. probes;            (on-page links, /llms.txt,      kept as the baseline
    headless browser if blocked)               /docs/llms.txt, sitemap.xml)
                                                          │
          ┌───────────────────────── verification loops ──┘
          ▼
 L1 targeted evidence    rule-based quotes about auth / gates / API / MCP from every page
 L2 follow-up crawl      fields with weak evidence trigger a second, field-targeted crawl
 L3 browser render       JS-rendered or bot-blocked docs are re-read in headless Edge
 L4 MCP registry         official MCP registry; only vendor-owned servers count
 L5 grounding check      every LLM claim must cite a quote that actually says it
 L6 reconciliation       LLM vs rules; conflicts go to a review queue, never guessed
          ▼
 human review queue      a person resolves flagged fields, each decision stores URL + quote
```

The LLM is a local model by default (`qwen2.5:3b` through Ollama): free and offline. A small model makes mistakes, which is the point of the design: the loops and the rules carry the accuracy, and the model only has to read quotes and fill a schema. Set `LLM_BACKEND=anthropic` and `ANTHROPIC_API_KEY` to use Claude instead.

## How accuracy is measured

- `data/rubric.md` defines every field and value, so answers can be scored.
- `data/gold.json`: 20 apps drawn at random (2 per category, `random.Random(2026)`), labeled from the live docs by an independent verifier that never saw the agent's output, with the hard cases checked by hand in a browser. The labels were committed before the agent existed (see the first commit). Every label carries its URL and quote.
- `uv run python -m agent score` scores three stages on the same rubric: pass 1 (naive), after the loops (the agent alone), and after human review. Fields the public docs cannot settle are left unscored instead of guessed.
- When the agent and the gold disagree, the docs decide. One gold label (Plaid) was corrected after the agent surfaced pages the verifier missed. The correction and its reason are recorded in `gold.json`.
- **Where a human was needed.** Every conflict, uncited claim and app with no readable docs goes to `runs/review_queue.json` (78 items). A reviewer (Claude reading the cited pages, opening a browser where scripts were blocked) proposed each decision with a URL and quote, and I signed off; they live in `data/review.json`, recorded by `scripts/record_decisions.py`. A second sweep fixed fields on the 80 apps *outside* the sample; the script refuses to touch sample apps, so the sweep cleans the table without moving the measured accuracy.
- **Evidence floor.** An app whose docs could not be read at all is marked `unverified`, never `build_now` from the LLM alone.

## Run it

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), and either Ollama with `qwen2.5:3b` or an Anthropic key. Microsoft Edge or Chromium is used for the browser loop (`--no-render` skips it).

```bash
uv sync --extra dev
uv run python -m agent run --app "Calendly" --hint developer.calendly.com --out runs/calendly.json   # any app
uv run python -m agent run          # the 100 apps -> runs/results.json
uv run python scripts/record_decisions.py   # re-finds every review quote in the cached docs
uv run python -m agent review       # writes runs/review_queue.json, applies data/review.json
uv run python -m agent score        # accuracy vs data/gold.json -> runs/accuracy.json
uv run python -m agent site         # site/index.html + site/apps.json + site/llms.txt
uv run pytest -q
```

Or use the **Research an app** GitHub Action (Actions tab → Run workflow): it starts Ollama in the runner and writes the evidenced record to the job summary.

## Layout

| Path | What |
|---|---|
| `agent/discover.py` | docs-host probing and per-question page ranking |
| `agent/fetch.py` | cached HTTP fetch, HTML to text, headless-browser fallback |
| `agent/signals.py` | evidence rules, negation handling, grounding vocabulary |
| `agent/research.py` | the agent: pass 1, loops L1 to L6, reconciliation |
| `agent/review.py` | review queue and human decisions |
| `agent/score.py`, `agent/patterns.py` | accuracy and the pattern analysis |
| `agent/site.py`, `agent/template.html` | the case-study page |
| `data/` | the 100 apps, rubric, gold sample, human review decisions, findings, one-liner edits, Composio coverage |
| `scripts/record_decisions.py` | review decisions, each re-found as a quote in the cached docs |
| `site/` | the built case study plus `apps.json`, `apps.csv`, `llms.txt` (published by the Pages workflow) |
| `runs/` | results, accuracy and the review queue from the last run |
