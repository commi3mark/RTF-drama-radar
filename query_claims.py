#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DATA_DIR = Path("data")


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def entity_lookup() -> tuple[dict[str, str], dict[str, str]]:
    payload = load(DATA_DIR / "entities.json")
    by_name = {}
    by_id = {}
    for e in payload.get("entities", []):
        eid = e["entity_id"]
        canonical = e["canonical_name"]
        by_id[eid] = canonical
        by_name[canonical.lower()] = eid
        for alias in e.get("aliases", []):
            value = alias["value"] if isinstance(alias, dict) else str(alias)
            by_name[value.lower()] = eid
    return by_name, by_id


def resolve(name: str, by_name: dict[str, str]) -> str:
    key = name.strip().lower()
    if key in by_name:
        return by_name[key]
    matches = [eid for alias, eid in by_name.items() if key in alias or alias in key]
    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    raise SystemExit(f"Could not uniquely resolve person: {name}")


def main() -> int:
    p = argparse.ArgumentParser(description="Search Drama Radar claims by one or more people.")
    p.add_argument("people", nargs="+", help="Person names or aliases")
    p.add_argument("--role", choices=["reporting_speaker", "attributed_speaker", "recipient", "subject"])
    p.add_argument("--direct-only", action="store_true")
    p.add_argument("--limit", type=int, default=25)
    args = p.parse_args()

    by_name, by_id = entity_lookup()
    wanted = [resolve(name, by_name) for name in args.people]
    claims = load(DATA_DIR / "claims.json").get("claims", [])

    results = []
    for c in claims:
        participants = set(c.get("participant_entity_ids", []))
        if not all(eid in participants for eid in wanted):
            continue
        if args.direct_only and c.get("directness") != "direct":
            continue
        if args.role:
            role_map = {
                "reporting_speaker": [c.get("reporting_speaker_entity_id")],
                "attributed_speaker": [c.get("attributed_speaker_entity_id")],
                "recipient": c.get("recipient_entity_ids", []),
                "subject": c.get("subject_entity_ids", []),
            }
            if not any(eid in role_map[args.role] for eid in wanted):
                continue
        results.append(c)

    results.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    for c in results[:args.limit]:
        print("=" * 80)
        print(c.get("published_at") or "Unknown date")
        print(c.get("source_title") or "Unknown source")
        print(f"Directness: {c.get('directness')}")
        print(f"Reporting speaker: {by_id.get(c.get('reporting_speaker_entity_id'), c.get('reporting_speaker_entity_id'))}")
        print(f"Attributed speaker: {by_id.get(c.get('attributed_speaker_entity_id'), c.get('attributed_speaker_entity_id'))}")
        recipients = [by_id.get(eid, eid) for eid in c.get("recipient_entity_ids", [])]
        subjects = [by_id.get(eid, eid) for eid in c.get("subject_entity_ids", [])]
        print(f"Recipients: {', '.join(recipients) or '-'}")
        print(f"Subjects: {', '.join(subjects) or '-'}")
        print(f"Quote: {c.get('exact_quote')}")
        print(f"Context before: {c.get('context_before')}")
        print(f"Context after: {c.get('context_after')}")
        print(f"Receipt: {c.get('receipt_url')}")
    print()
    print(f"Matches: {len(results)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
