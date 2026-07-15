#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from radar_core import load_json

DATA = Path("data")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("terms", nargs="*")
    p.add_argument("--active-only", action="store_true")
    p.add_argument("--limit", type=int, default=20)
    args = p.parse_args()

    stories = load_json(DATA / "stories.json", {}).get("stories", [])
    terms = [t.lower() for t in args.terms]
    results = []
    for story in stories:
        hay = " ".join([
            story.get("title") or "",
            story.get("latest_summary") or "",
            " ".join(story.get("people", [])),
            " ".join(story.get("topic_terms", [])),
        ]).lower()
        if terms and not all(term in hay for term in terms):
            continue
        if args.active_only and story.get("status") not in {"active", "emerging"}:
            continue
        results.append(story)

    results.sort(key=lambda s: (s.get("momentum", 0), s.get("last_updated_at") or ""), reverse=True)
    for story in results[:args.limit]:
        print("=" * 88)
        print(story.get("story_id"), "-", story.get("title"))
        print("Status:", story.get("status"))
        print("People:", ", ".join(story.get("people", [])))
        print("Momentum:", story.get("momentum"))
        print("Latest:", story.get("latest_summary"))
        print("Quotes:", len(story.get("supporting_quote_ids", [])))
    print()
    print(f"Matches: {len(results)}")

if __name__ == "__main__":
    main()
