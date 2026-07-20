from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RADAR_ROOT = Path(__file__).resolve().parents[1]
SYSTEM_ROOT = RADAR_ROOT.parent
GRABBER_ROOT = SYSTEM_ROOT / "02 - TRANSCRIPT GRABBER"

RADAR_PATH = RADAR_ROOT / "output" / "drama-radar.json"
STATS_PATH = RADAR_ROOT / "output" / "radar-stats.json"
SOURCES_PATH = RADAR_ROOT / "config" / "sources.json"
ARCHIVE_DIR = RADAR_ROOT / "archive"
BRAIN_DIR = RADAR_ROOT / "state"
TRANSCRIPTS_DIR = GRABBER_ROOT / "transcripts"
TRANSCRIPT_INDEX_PATH = TRANSCRIPTS_DIR / "transcript-index.json"
TRANSCRIPT_MANIFEST_PATH = TRANSCRIPTS_DIR / "transcript-manifest.json"
RECEIPTS_DIR = RADAR_ROOT / "receipts"

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temp.replace(path)


def stable_id(*parts: str) -> str:
    raw = "|".join(str(part or "").strip() for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
