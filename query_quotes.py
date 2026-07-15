#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from radar_core import load_json

DATA = Path("data")

def maps():
    payload = load_json(DATA / "entities.json", {})
    by_name, by_id = {}, {}
    for entity in payload.get("entities", []):
        eid = entity["entity_id"]
        by_id[eid] = entity["canonical_name"]
        by_name[entity["canonical_name"].lower()] = eid
        for alias in entity.get("aliases", []):
            value = alias["value"] if isinstance(alias, dict) else str(alias)
            by_name[value.lower()] = eid
    return by_name, by_id

def resolve(name, by_name):
    key = name.lower().strip()
    if key in by_name:
        return by_name[key]
    matches = sorted(set(eid for alias, eid in by_name.items() if key in alias or alias in key))
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"Could not uniquely resolve: {name}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("people", nargs="*")
    p.add_argument("--contains")
    p.add_argument("--source")
    p.add_argument("--limit", type=int, default=30)
    args = p.parse_args()

    by_name, by_id = maps()
    wanted = [resolve(name, by_name) for name in args.people]
    quotes = load_json(DATA / "quote-index.json", {}).get("quotes", [])
    results = []
    for quote in quotes:
        ids = set(quote.get("direct_entity_ids", []) + quote.get("nearby_entity_ids", []))
        if not all(eid in ids for eid in wanted):
            continue
        if args.contains and args.contains.lower() not in quote.get("context_text", "").lower():
            continue
        if args.source and args.source.lower() not in (quote.get("channel") or "").lower():
            continue
        results.append(quote)

    results.sort(key=lambda q: (q.get("published_at") or "", q.get("timestamp_seconds") or 0), reverse=True)
    for quote in results[:args.limit]:
        print("=" * 88)
        print(quote.get("published_at") or "Unknown date")
        print(quote.get("video_title") or "Unknown video")
        print("People:", ", ".join(by_id.get(eid, eid) for eid in quote.get("nearby_entity_ids", [])))
        print("QUOTE:", quote.get("exact_text"))
        print("Before:", quote.get("context_before"))
        print("After:", quote.get("context_after"))
        print("Timestamp:", quote.get("timestamp_display"))
        print("Receipt:", quote.get("receipt_url"))
    print()
    print(f"Matches: {len(results)}")

if __name__ == "__main__":
    main()
