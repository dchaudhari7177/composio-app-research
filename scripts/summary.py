"""Render one research result as Markdown (used for the GitHub Actions job summary)."""

import json
import sys

rec = json.load(open(sys.argv[1], encoding="utf-8"))["apps"][0]
fin = rec.get("final", {})
print(f"## {rec['name']}: `{fin.get('verdict')}`\n")
print(f"{rec.get('one_liner', '')}\n")
print("| Field | Agent answer | Evidence |\n|---|---|---|")
for k in ("auth", "access", "api_type", "official_mcp"):
    ev = (rec.get("evidence") or {}).get(k) or []
    cite = f"[source]({ev[0]['url']}): \"{ev[0]['quote'][:140]}\"" if ev else "none"
    val = ", ".join(fin.get(k) or []) if k == "auth" else fin.get(k)
    print(f"| {k} | `{val}` | {cite} |")
print(f"\n**Pass 1 (naive)**: `{json.dumps(rec.get('pass1'))}`\n")
print("**Flags for human review**")
for f in rec.get("flags", []):
    print(f"- {f['field']} · {f['kind']}: {f['detail']}")
print("\n**Trace**")
for t in rec.get("trace", []):
    print(f"1. {t}")
