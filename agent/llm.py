"""LLM backends. The research step only needs "evidence in, JSON out".

Default is a local model through Ollama (free, runs offline, no key). Set
ANTHROPIC_API_KEY and LLM_BACKEND=anthropic to use Claude instead; the prompt
and the JSON contract are the same.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import httpx

RUBRIC = """Label a SaaS app's public API for building an AI-agent toolkit. Use ONLY the evidence snippets given.
Fields:
- one_liner: what the product does, <= 14 words.
- auth: every auth method the public API supports, from: oauth2 (OAuth 2.0 flow for apps), api_key (static secret / API key / personal access token / bot token created in a dashboard), basic (HTTP Basic), other (HMAC signing, JWT service accounts, mTLS, session).
- access: the gate before a developer gets working credentials: self_serve (free account, free tier, free sandbox or free trial), paid_plan (API only on a paid plan you can buy), approval (app review / application / admin approval / waitlist), partner (contact sales, enterprise-only, partnership), no_api (no public network API; CLI or library only).
- api_type: rest | graphql | both | none.
- api_breadth: broad | moderate | narrow | none.
- official_mcp: yes if the vendor documents its own MCP server, else no.
- blocker: the main thing stopping a toolkit today, <= 12 words, or "none".
For auth, access, api_type and official_mcp give "cite": the ids of the snippets that support the value (e.g. ["S3","S7"]). Never cite a snippet that does not say it.
Return JSON only:
{"one_liner": str, "auth": [{"value": str, "cite": [str]}], "access": {"value": str, "cite": [str]}, "api_type": {"value": str, "cite": [str]}, "api_breadth": str, "official_mcp": {"value": str, "cite": [str]}, "blocker": str}"""


class LLM:
    def __init__(self, backend: str | None = None, model: str | None = None):
        self.backend = backend or os.environ.get("LLM_BACKEND", "ollama")
        if self.backend == "ollama":
            self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
            self.host = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11435")
        elif self.backend == "anthropic":
            self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
        self.calls = 0

    @property
    def name(self) -> str:
        return f"{self.backend}:{self.model}"

    def complete_json(self, prompt: str) -> dict:
        key = hashlib.sha1("\n".join((self.name, RUBRIC, prompt)).encode()).hexdigest()[:24]
        cache = Path(__file__).resolve().parent.parent / "runs" / "cache" / f"llm-{key}.json"
        if cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))
        out = self._complete(prompt)
        if out:
            cache.write_text(json.dumps(out), encoding="utf-8")
        return out

    def _complete(self, prompt: str) -> dict:
        self.calls += 1
        if self.backend == "ollama":
            r = httpx.post(
                f"{self.host}/api/chat",
                json={"model": self.model, "format": "json", "stream": False,
                      "options": {"temperature": 0, "num_ctx": 6144},
                      "messages": [{"role": "system", "content": RUBRIC}, {"role": "user", "content": prompt}]},
                timeout=300,
            )
            r.raise_for_status()
            text = r.json()["message"]["content"]
        elif self.backend == "anthropic":
            r = httpx.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                json={"model": self.model, "max_tokens": 1200, "system": RUBRIC,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=120,
            )
            r.raise_for_status()
            text = r.json()["content"][0]["text"]
        else:
            raise ValueError(f"unknown backend {self.backend}")
        return parse_json(text)


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return {}
