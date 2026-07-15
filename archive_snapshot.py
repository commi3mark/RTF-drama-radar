#!/usr/bin/env python3
from __future__ import annotations
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from radar_core import load_json, write_json

DATA = Path("data")
ARCHIVE = Path("archive")

def main():
    now = datetime.now(timezone.utc)
    label = now.strftime("%Y-%m-%d")
    target = ARCHIVE / label
    target.mkdir(parents=True, exist_ok=True)
    for filename in [
        "people.json", "quote-index.json", "evidence-index.json",
        "context-bundles.json", "narrative-units.json", "stories.json",
        "relationships.json", "campaigns.json", "risk-signals.json",
        "commi3-risk-profiles.json"
    ]:
        src = DATA / filename
        if src.exists():
            shutil.copy2(src, target / filename)
    print(f"Archived current intelligence state to {target}")

if __name__ == "__main__":
    main()
