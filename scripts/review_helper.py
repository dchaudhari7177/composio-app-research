"""Print each review-queue item with the candidate quotes from the cached pages.

A reviewer reads this, opens the URLs when the quotes are not enough, and
writes decisions to data/review.json as
{"id", "name", "field", "value", "url", "quote", "reviewer", "note"}.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.fetch import CACHE, Page  # noqa: E402
from agent.signals import extract  # noqa: E402

results = json.loads((ROOT / "runs" / "results.json").read_text(encoding="utf-8"))
queue = json.loads((ROOT / "runs" / "review_queue.json").read_text(encoding="utf-8"))
only = set(sys.argv[1:])

# index cached pages by host so we can show every quote we have for an app
pages = []
for p in CACHE.glob("*.json"):
    if p.name.startswith("llm-"):
        continue
    try:
        pages.append(Page.from_json(json.loads(p.read_text(encoding="utf-8"))))
    except Exception:
        pass

by_id = {r["id"]: r for r in results["apps"]}
for item in queue:
    if only and item["name"] not in only and str(item["id"]) not in only:
        continue
    rec = by_id[item["id"]]
    print(f"\n=== {item['id']} {item['name']} · {item['field']} · {item['kind']} · agent={item.get('agent_value')}  (pass1={rec.get('pass1', {}).get(item['field'])})")
    print("   ", item["detail"])
    docs = rec.get("docs_url") or ""
    host = docs.split("/")[2] if docs.startswith("http") else ""
    shown = 0
    for pg in pages:
        u = pg.final_url or pg.url
        if not host or host not in u:
            continue
        for s in extract(pg):
            if s.field == item["field"] and s.strength >= 2 and shown < 8:
                print(f"    [{s.value}/{s.strength}] {u}\n        {s.quote[:260]}")
                shown += 1
