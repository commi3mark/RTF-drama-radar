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
    matches = sorted(set(eid for alias, eid in by_name.items() if key in alias or alias in key))
    if len(matches) == 1:
        return matches[0]
    raise SystemExit(f"Could not uniquely resolve: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Search narrative-ready Drama Radar context.")
    parser.add_argument("people", nargs="+", help="One or more tracked people")
    parser.add_argument("--contains", help="Require a word or phrase")
    parser.add_argument("--development", help="Filter by development type, e.g. response, denial, escalation")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--show-context", action="store_true")
    args = parser.parse_args()

    by_name, by_id = entity_maps()
    wanted = [resolve(name, by_name) for name in args.people]

    bundles = load(DATA_DIR / "context-bundles.json").get("context_bundles", [])
    narratives = load(DATA_DIR / "narrative-units.json").get("narrative_units", [])
    narrative_by_bundle = {n["context_bundle_id"]: n for n in narratives}

    results = []
    for bundle in bundles:
        involved = set(bundle.get("involved_entity_ids", []))
        if not all(eid in involved for eid in wanted):
            continue
        if args.contains and args.contains.lower() not in bundle.get("context_text", "").lower():
            continue
        if args.development and args.development not in bundle.get("development_types", []):
            continue
        narrative = narrative_by_bundle.get(bundle["context_bundle_id"])
        if narrative:
            results.append((bundle, narrative))

    results.sort(
        key=lambda item: (
            item[1].get("confidence", 0.0),
            item[0].get("published_at") or "",
        ),
        reverse=True,
    )

    for bundle, narrative in results[:args.limit]:
        print("=" * 88)
        print(bundle.get("published_at") or "Unknown date")
        print(bundle.get("title") or "Unknown video")
        print("People:", ", ".join(bundle.get("involved_entity_names", [])))
        print("Headline:", narrative.get("headline"))
        print("Summary:", narrative.get("summary"))
        print("New development:", narrative.get("new_development"))
        if narrative.get("uncertainties"):
            print("Uncertainties:")
            for uncertainty in narrative["uncertainties"]:
                print(" -", uncertainty)
        print("Timestamp:", narrative.get("evidence", {}).get("timestamp"))
        print("Receipt:", narrative.get("evidence", {}).get("receipt_url"))
        print("Context receipt:", narrative.get("evidence", {}).get("context_receipt_url"))
        if args.show_context:
            print("Context:", bundle.get("context_text"))

    print()
    print(f"Matches: {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
