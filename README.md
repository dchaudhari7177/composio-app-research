# App toolkit readiness: an agent that researches 100 SaaS apps

Composio take-home (AI Product Ops Intern). Before building a toolkit for an app you need to know how it authenticates, whether a developer can get credentials alone or has to get through a gate, what the API looks like, and whether the vendor already ships an MCP server. This repo is an agent that answers those questions for any app, with a docs URL and a quote behind every answer. It has been run on the 100 apps in the brief.

**Case study (the deliverable):** `site/index.html`, deployed at the link in the submission. It shows the patterns, the 100-row table, the agent, and the verification.

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

## Run it

Needs Python 3.11+, [uv](https://docs.astral.sh/uv/), and either Ollama with `qwen2.5:3b` or an Anthropic key. Microsoft Edge or Chromium is used for the browser loop (`--no-render` skips it).

```bash
uv sync --extra dev
uv run python -m agent run --app "Calendly" --hint developer.calendly.com --out runs/calendly.json   # any app
uv run python -m agent run          # the 100 apps -> runs/results.json
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
| `data/` | the 100 apps, rubric, gold sample, human review decisions, findings |
| `runs/` | results, accuracy and the review queue from the last run |
