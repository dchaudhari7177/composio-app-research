"""Command line entry point.

    uv run python -m agent run                 # research all 100 apps -> runs/results.json
    uv run python -m agent run --ids 2,61,82   # a few apps
    uv run python -m agent run --app "Stripe" --hint stripe.com/docs/api   # any app, not just the list
    uv run python -m agent score               # accuracy vs the hand-checked gold sample
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

from .fetch import Fetcher
from .llm import LLM
from .research import Researcher

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / "runs"


def load_apps() -> list[dict]:
    with open(ROOT / "data" / "apps.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["id"] = int(r["id"])
    return rows


def parse_ids(s: str) -> set[int]:
    out: set[int] = set()
    for part in s.split(","):
        if "-" in part:
            a, b = part.split("-")
            out.update(range(int(a), int(b) + 1))
        elif part.strip():
            out.add(int(part))
    return out


async def run(apps: list[dict], out_path: Path, concurrency: int, render: bool, backend: str | None, model: str | None):
    f = Fetcher()
    llm = LLM(backend, model)
    r = Researcher(f, llm, render=render)
    sem = asyncio.Semaphore(concurrency)
    results: dict[int, dict] = {}
    if out_path.exists():
        for rec in json.loads(out_path.read_text(encoding="utf-8")).get("apps", []):
            results[rec["id"]] = rec
    t0 = time.time()
    done = 0

    async def one(app):
        nonlocal done
        async with sem:
            try:
                rec = await asyncio.wait_for(r.run(app), timeout=360)
            except Exception as e:  # one broken site must not stop the batch
                rec = {"id": app["id"], "name": app["name"], "category": app["category"], "hint": app["hint"],
                       "error": f"{type(e).__name__}: {e}"[:300]}
            results[app["id"]] = rec
            done += 1
            fin = rec.get("final", {})
            print(f"[{done}/{len(apps)}] {app['id']:>3} {app['name']:<26} {fin.get('verdict')!s:<16} "
                  f"auth={','.join(fin.get('auth', []))} access={fin.get('access')} mcp={fin.get('official_mcp')} "
                  f"flags={len(rec.get('flags', []))} ({time.time() - t0:.0f}s)", flush=True)
            if done % 5 == 0:
                save(results, out_path, llm, f)

    await asyncio.gather(*(one(a) for a in apps))
    save(results, out_path, llm, f)
    await f.close()


def save(results: dict, out_path: Path, llm: LLM, f: Fetcher):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "llm": llm.name, "fetch_stats": f.stats,
            "apps": [results[k] for k in sorted(results)]}
    out_path.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pr = sub.add_parser("run", help="research apps")
    pr.add_argument("--ids", help="e.g. 1-10,61")
    pr.add_argument("--app", help="research an app that is not in the list")
    pr.add_argument("--hint", help="docs URL or domain for --app")
    pr.add_argument("--out", default=str(RUNS / "results.json"))
    pr.add_argument("--concurrency", type=int, default=6)
    pr.add_argument("--no-render", action="store_true", help="skip the headless-browser loop")
    pr.add_argument("--backend", help="ollama | anthropic")
    pr.add_argument("--model")
    ps = sub.add_parser("score", help="accuracy against data/gold.json")
    ps.add_argument("--results", default=str(RUNS / "results.json"))
    prv = sub.add_parser("review", help="write the review queue and apply data/review.json")
    prv.add_argument("--results", default=str(RUNS / "results.json"))
    pb = sub.add_parser("site", help="build site/index.html from the results")
    pb.add_argument("--results", default=str(RUNS / "results.json"))
    a = ap.parse_args(argv)

    if a.cmd == "run":
        if a.app:
            apps = [{"id": 1000, "name": a.app, "category": "ad hoc", "hint": a.hint or a.app.lower() + ".com", "hint_note": ""}]
        else:
            apps = load_apps()
            if a.ids:
                keep = parse_ids(a.ids)
                apps = [x for x in apps if x["id"] in keep]
        asyncio.run(run(apps, Path(a.out), a.concurrency, not a.no_render, a.backend, a.model))
    elif a.cmd == "score":
        from .score import score_file

        print(json.dumps(score_file(Path(a.results)), indent=1))
    elif a.cmd == "review":
        from .review import main as review_main

        review_main(Path(a.results))
    elif a.cmd == "site":
        from .site import build

        build(Path(a.results))


if __name__ == "__main__":
    sys.exit(main())
