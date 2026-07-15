#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from radar_core import load_json

DATA = Path("data")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("name")
    args = p.parse_args()

    people = load_json(DATA / "people.json", {}).get("people", [])
    needle = args.name.lower()
    matches = [
        person for person in people
        if needle in person.get("canonical_name", "").lower()
        or any(needle in (a.get("value","") if isinstance(a, dict) else str(a)).lower() for a in person.get("aliases", []))
    ]
    if not matches:
        raise SystemExit("No matching person.")
    for person in matches:
        print("=" * 88)
        print(person.get("canonical_name"))
        print("First seen:", person.get("first_seen"))
        print("Last seen:", person.get("last_seen"))
        print("Mentions:", person.get("mention_count"))
        print("Sources:", person.get("source_count"))
        print("Quotes:", len(person.get("quote_ids", [])))
        print("Narratives:", len(person.get("narrative_unit_ids", [])))
        print("Accounts:")
        for account in person.get("accounts", []):
            print(" -", account.get("platform"), account.get("handle"), account.get("url"))
        print("Campaigns:", len(person.get("campaigns", [])))
        print("Relationships:", len(person.get("relationships", [])))

if __name__ == "__main__":
    main()
