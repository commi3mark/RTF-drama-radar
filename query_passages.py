#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def entity_maps() -> tuple[dict[str, str], dict[str, str]]:
    payload = load(DATA_DIR / "entities.json")
    by_name = {}
    by_id = {}
    for entity in payload.get("entities", []):
        eid = entity["entity_id"]
        canonical = entity["canonical_name"]
        by_id[eid] = canonical
        by_name[canonical.lower()] = eid
        for alias in entity.get("aliases", []):
            value = alias["value"] if isinstance(alias, dict) else str(alias)
            by_name[value.lower()] = eid
    return by_name, by_id


def resolve(name: str, by_name: dict[str, str]) -> str:
    key = name.strip().lower()
    if key in by_name:
        return by_name[key]
    matches = sorted(set(
        eid for alias, eid in by_name.items()
        if key in alias or alias in key
    ))
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"Could not uniquely resolve: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search timestamped Drama Radar passages.")
    parser.add_argument("people", nargs="+", help="One or more people")
    parser.add_argument("--contains", help="Require text in the passage context")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--min-confidence", type=float, default=0.0)
    args = parser.parse_args()

    by_name, by_id = entity_maps()
    wanted = [resolve(name, by_name) for name in args.people]
    passages = load(DATA_DIR / "passage-index.json").get("passages", [])

    results = []
    for passage in passages:
        involved = set(passage.get("involved_entity_ids", []))
        if not all(eid in involved for eid in wanted):
            continue
        if passage.get("confidence", 0.0) < args.min_confidence:
            continue
        if args.contains and args.contains.lower() not in passage.get("full_context", "").lower():
            continue
        results.append(passage)

    results.sort(
        key=lambda x: (
            x.get("confidence", 0.0),
            x.get("claim_term_count", 0),
            x["source"].get("published_at") or "",
        ),
        reverse=True,
    )

    for passage in results[:args.limit]:
        source = passage["source"]
        print("=" * 80)
        print(source.get("published_at") or "Unknown date")
        print(source.get("title") or "Unknown video")
        print("People:", ", ".join(by_id.get(eid, eid) for eid in passage.get("involved_entity_ids", [])))
        print("Excerpt:", passage.get("claim_excerpt"))
        print("Before:", passage.get("context_before"))
        print("After:", passage.get("context_after"))
        print("Confidence:", passage.get("confidence"))
        print("Receipt:", source.get("receipt_url"))

    print()
    print(f"Matches: {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
